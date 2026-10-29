"""New-cards HTML: recent-set EDHREC recommendations per commander."""

from __future__ import annotations

import time
from collections import Counter
from copy import copy as shallow_copy
from datetime import datetime, timedelta
from pathlib import Path

from clients.session import create_cached_session
from clients import scryfall_bulk
from core.card import Card, SCRYFALL_SLEEP, _get_with_retry
from core.config import RunConfig, _ArgsShim
from core.deck import EDHREC_SLEEP, _edhrec_get_with_retry
from edhparse.edhrec_commander_themes import (
    discover_theme_slugs,
    edhrec_commander_json_url,
    extract_page_cards_and_meta,
    theme_label_from_container,
)
from edhparse.parsing import load_commander_slugs_from_file
from edhparse.services import err_result, ok_result
from utils.progress_utils import progress_bar


def _fmt_pct(val):
    """Heuristic formatter for EDHREC-style percentages (compat with extract_page_cards_and_meta)."""
    if val is None:
        return None
    try:
        v = float(val)
    except (TypeError, ValueError):
        return None
    if -2.0 <= v <= 2.0:
        v *= 100.0
    return f"{v:.1f}%"


def _is_mechanically_new(card: Card, cutoff_date: str) -> bool:
    """True when this oracle card's first printing is on/after cutoff_date."""
    debut = getattr(card, "first_print_released_at", None)
    return bool(debut and debut >= cutoff_date)


def _annotate_first_printing(cards_by_name: dict) -> None:
    """
    Fill first-print metadata from the local default_cards bulk index.

    If the debut index is missing a card (stale bulk, newly spoiled), fall back to
    this printing's release date when Scryfall marks it as not a reprint.
    """
    for card in cards_by_name.values():
        debut_date = None
        debut_set = None
        if card.oracle_id:
            debut_date, debut_set = scryfall_bulk.get_debut_printing(card.oracle_id)

        if not debut_date:
            # Bulk debut index can lag newly spoiled cards that API lookup already has.
            if not getattr(card, "is_reprint", False):
                debut_date = getattr(card, "released_at", None)
                debut_set = getattr(card, "set_code", None)

        card.first_print_released_at = debut_date
        card.first_print_set_code = debut_set
        if debut_set:
            card.set_code = debut_set
            if debut_date:
                card.released_at = debut_date


def run_new_cards_html(
    commander_list: str | Path,
    out_path: str,
    cfg: RunConfig,
) -> dict:
    """
    For each commander, pull full EDHREC recs (Normal/Budget/Expensive × themes),
    then show cards from recent Scryfall release sets.
    """
    names_and_types = load_commander_slugs_from_file(commander_list)
    if not names_and_types:
        msg = "No commanders found in commander_list."
        print(msg)
        return err_result(msg)

    session = create_cached_session(fresh_cache=cfg.fresh_cache)
    # "Fresh cache" also forces a Scryfall bulk/debut re-download — HTTP cache alone
    # does not update the local oracle/debut SQLite indexes.
    force_bulk = bool(cfg.fresh_cache)
    if not scryfall_bulk.ensure_loaded(session, force_refresh=force_bulk):
        msg = "Scryfall oracle_cards bulk is unavailable."
        print(msg)
        return err_result(msg)
    if not scryfall_bulk.ensure_debut_index(session, force_refresh=force_bulk):
        msg = "Scryfall debut-date bulk index is unavailable."
        print(msg)
        return err_result(msg)

    args = _ArgsShim(cfg)
    output_per_commander = []

    total_commanders = len(names_and_types)
    with progress_bar(total_commanders, initial_value=0) as commander_bar:
        idx = 0
        for commander_slug, _variant in names_and_types:
            info_name = commander_slug.replace("-", " ")
            commander_bar.update(idx, info=f"Loading EDHREC data for {info_name}")

            theme_path_slugs = discover_theme_slugs(
                session, commander_slug, max_themes=cfg.max_themes or 5
            )
            theme_options = [("", "Average")]
            page_cache = {}

            def _fetch_cmd_json(theme_slug: str, tier_slug: str, _slug=commander_slug):
                key = (theme_slug or "", tier_slug or "")
                if key in page_cache:
                    return page_cache[key]
                url = edhrec_commander_json_url(
                    _slug,
                    theme_slug=theme_slug or "",
                    tier_slug=tier_slug or "",
                )
                response, err = _edhrec_get_with_retry(session, url, timeout=10)
                if err:
                    page_cache[key] = None
                    return None
                if not getattr(response, "from_cache", True):
                    time.sleep(EDHREC_SLEEP)
                try:
                    data = response.json()
                except Exception:
                    page_cache[key] = None
                    return None
                page_cache[key] = data
                return data

            commander_title = None
            main_hub = _fetch_cmd_json("", "")
            if main_hub:
                container = main_hub.get("container") or {}
                json_dict = container.get("json_dict") or {}
                header = container.get("header") or json_dict.get("header")
                if header:
                    commander_title = header.replace(" (Commander)", "").strip()

            for th in theme_path_slugs:
                th_data = _fetch_cmd_json(th, "")
                if not th_data:
                    continue
                container = th_data.get("container") or {}
                theme_options.append((th, theme_label_from_container(container, th)))

            tier_order = [("", "average"), ("budget", "budget"), ("expensive", "expensive")]

            all_names = set()
            pane_orders_meta = {}
            for tier_path, tier_key in tier_order:
                for theme_slug, _tlabel in theme_options:
                    pane_key = f"{tier_key}::{theme_slug}"
                    data = _fetch_cmd_json(theme_slug, tier_path)
                    if not data:
                        pane_orders_meta[pane_key] = ([], {})
                        continue
                    ordered_n, meta = extract_page_cards_and_meta(data, fmt_synergy=_fmt_pct)
                    pane_orders_meta[pane_key] = (ordered_n, meta)
                    all_names.update(ordered_n)

            if not all_names:
                idx += 1
                continue

            cards_by_name = {}
            name_list = sorted(all_names)
            with progress_bar(len(name_list), initial_value=0) as card_bar:
                for cidx, name in enumerate(name_list):
                    card_bar.update(cidx, info=f"Scryfall lookup for {name}")
                    card = Card(session, args, is_commander=False)
                    card.search_card(name)
                    if card.error or not card.set_code:
                        continue
                    cards_by_name[name] = card

            if not cards_by_name:
                idx += 1
                continue

            _annotate_first_printing(cards_by_name)

            panes = {}
            for tier_path, tier_key in tier_order:
                for theme_slug, _ in theme_options:
                    pane_key = f"{tier_key}::{theme_slug}"
                    ordered_n, meta = pane_orders_meta.get(pane_key) or ([], {})
                    plist = []
                    for name in ordered_n:
                        base = cards_by_name.get(name)
                        if not base:
                            continue
                        c = shallow_copy(base)
                        m = meta.get(name) or {}
                        c.edh_inclusion = m.get("inclusion")
                        c.edh_synergy = m.get("synergy")
                        plist.append(c)
                    panes[pane_key] = plist

            commander_card = Card(session, args, is_commander=True)
            display_name = commander_title or commander_slug.replace("-", " ").title()
            commander_card.search_card(display_name)
            if commander_card.error:
                commander_card = None

            output_per_commander.append(
                {
                    "slug": commander_slug,
                    "name": display_name,
                    "commander_card": commander_card,
                    "tier_options": [
                        {"id": "average", "label": "Average"},
                        {"id": "budget", "label": "Budget"},
                        {"id": "expensive", "label": "Expensive"},
                    ],
                    "theme_options": [
                        {"slug": tslug, "label": tlab} for tslug, tlab in theme_options
                    ],
                    "panes": panes,
                }
            )

            idx += 1

    if not output_per_commander:
        msg = "No recommended cards found for any commander."
        print(msg)
        return err_result(msg)

    cutoff_date = (datetime.utcnow().date() - timedelta(days=183)).isoformat()
    sets_url = "https://api.scryfall.com/sets"
    params = {"order": "released", "dir": "desc"}
    response, err = _get_with_retry(session, sets_url, params=params, timeout=10)
    if err:
        msg = f"Error fetching Scryfall sets: {err}"
        print(msg)
        return err_result(msg)
    if not getattr(response, "from_cache", True):
        time.sleep(SCRYFALL_SLEEP)
    sets_data = response.json()

    eligible_sets_info = {}
    for s in sets_data.get("data", []):
        code = (s.get("code") or "").strip()
        released_at = s.get("released_at") or ""
        if not code or not released_at:
            continue
        if released_at < cutoff_date and code.lower() != "sld":
            continue
        eligible_sets_info[code.lower()] = {
            "code": code.lower(),
            "name": s.get("name") or code.upper(),
            "set_type": s.get("set_type"),
            "released_at": released_at,
        }

    if not eligible_sets_info:
        msg = "No Scryfall sets found in the last ~6 months."
        print(msg)
        return err_result(msg)

    for entry in output_per_commander:
        for card_list in entry["panes"].values():
            for card in card_list:
                scode = (
                    getattr(card, "first_print_set_code", None)
                    or getattr(card, "set_code", None)
                    or ""
                ).lower()
                meta = eligible_sets_info.get(scode)
                if meta:
                    card.set_code = scode
                    card.set_name = meta.get("name") or scode.upper()

    set_card_counts = Counter()
    for entry in output_per_commander:
        for card_list in entry["panes"].values():
            for card in card_list:
                if not _is_mechanically_new(card, cutoff_date):
                    continue
                scode = (getattr(card, "first_print_set_code", None) or getattr(card, "set_code", None) or "").lower()
                if not scode:
                    continue
                if scode not in eligible_sets_info:
                    continue
                set_card_counts[scode] += 1

    sets_to_consider = {code for code, cnt in set_card_counts.items() if cnt > 0}
    if not sets_to_consider:
        msg = "No mechanically new EDHREC recommended cards found in the last ~6 months of sets."
        print(msg)
        return err_result(msg)

    filtered_output = []
    for entry in output_per_commander:
        panes_f = {}
        for pk, card_list in entry["panes"].items():
            flist = []
            for c in card_list:
                if not _is_mechanically_new(c, cutoff_date):
                    continue
                scode = (getattr(c, "first_print_set_code", None) or getattr(c, "set_code", None) or "").lower()
                if not scode or scode not in sets_to_consider:
                    continue
                flist.append(c)
            panes_f[pk] = flist
        entry = dict(entry)
        entry["panes"] = panes_f
        filtered_output.append(entry)

    set_codes_sorted = sorted(
        list(sets_to_consider),
        key=lambda code: eligible_sets_info[code]["released_at"],
        reverse=True,
    )
    label_parts = []
    for code in set_codes_sorted:
        label_parts.append(f"{eligible_sets_info[code]['name']} ({code.upper()})")
        if len(label_parts) >= 4:
            break
    set_label = " / ".join(label_parts)

    resolved_out = out_path or "outputs/new_cards.html"
    from renderers.new_cards_renderer import write_new_cards_html

    write_new_cards_html(
        out_path=resolved_out,
        filtered_output=filtered_output,
        eligible_sets_info=eligible_sets_info,
        set_card_counts=set_card_counts,
        set_codes_sorted=set_codes_sorted,
        set_label=set_label,
    )

    return ok_result(f"Wrote new-cards summary to {resolved_out}", outputs=[resolved_out])
