"""Fetch EDHREC cards across tiers/themes into wipcoms/ decklists."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional, Sequence

from clients.session import create_cached_session
from core.config import RunConfig
from core.deck import EDHREC_SLEEP, _edhrec_get_with_retry
from edhparse.edhrec_commander_themes import (
    discover_theme_slugs,
    edhrec_commander_json_url,
    theme_label_from_container,
)
from edhparse.edhrec_util import inclusion_pct, synergy_pct
from edhparse.parsing import load_commander_slugs_from_file
from edhparse.services import err_result, ok_result
from utils.progress_utils import progress_bar

TIER_ORDER = [("", "average"), ("budget", "budget"), ("expensive", "expensive")]
DEFAULT_MIN_INCLUSION = 5.0
DEFAULT_MIN_LAND_SYNERGY = 50.0
DEFAULT_MIN_LAND_INCLUSION = 30.0


def _section_label(section: dict) -> str:
    return (section.get("cardlist") or section.get("header") or "").strip()


def _is_land_section(section: dict) -> bool:
    return "land" in _section_label(section).casefold()


def extract_cards_with_stats(data: dict) -> list[tuple[str, float | None, float | None, bool]]:
    """Return (name, inclusion%, synergy%, is_land_section) for each cardview."""
    container = data.get("container") or {}
    json_dict = container.get("json_dict") or {}
    cardlists = json_dict.get("cardlists") or []

    rows: list[tuple[str, float | None, float | None, bool]] = []
    for section in cardlists:
        land = _is_land_section(section)
        for cv in section.get("cardviews") or []:
            name = (cv.get("name") or "").strip()
            if not name:
                continue
            rows.append((name, inclusion_pct(cv), synergy_pct(cv), land))
    return rows


def merge_card_stats(
    merged: dict[str, dict],
    rows: list[tuple[str, float | None, float | None, bool]],
) -> None:
    for name, inc, syn, is_land in rows:
        key = name.casefold()
        entry = merged.get(key)
        if entry is None:
            merged[key] = {
                "name": name,
                "max_inclusion": inc,
                "max_synergy": syn,
                "is_land": is_land,
            }
            continue
        if is_land:
            entry["is_land"] = True
        if inc is not None and (entry["max_inclusion"] is None or inc > entry["max_inclusion"]):
            entry["max_inclusion"] = inc
        if syn is not None and (entry["max_synergy"] is None or syn > entry["max_synergy"]):
            entry["max_synergy"] = syn


def passes_filter(
    entry: dict,
    *,
    min_inclusion: float,
    min_land_synergy: float,
    min_land_inclusion: float,
) -> bool:
    if entry["is_land"]:
        syn = entry.get("max_synergy")
        inc = entry.get("max_inclusion")
        syn_ok = syn is not None and syn > min_land_synergy
        inc_ok = inc is not None and inc > min_land_inclusion
        return syn_ok or inc_ok
    inc = entry.get("max_inclusion")
    return inc is not None and inc > min_inclusion


def fetch_commander_cards(
    session,
    commander_slug: str,
    *,
    max_themes: int = 5,
    timeout: int = 15,
    min_inclusion: float = DEFAULT_MIN_INCLUSION,
    min_land_synergy: float = DEFAULT_MIN_LAND_SYNERGY,
    min_land_inclusion: float = DEFAULT_MIN_LAND_INCLUSION,
) -> tuple[list[str], str, list[str], int, int]:
    """
    Return (filtered_cards, commander_title, source_labels, before_count, after_count).
    """
    slug = (commander_slug or "").strip().strip("/")
    if not slug:
        return [], "", [], 0, 0

    theme_slugs = discover_theme_slugs(session, slug, max_themes=max_themes)
    page_cache: dict[tuple[str, str], dict | None] = {}

    def fetch_json(theme_slug: str, tier_slug: str) -> dict | None:
        key = (theme_slug or "", tier_slug or "")
        if key in page_cache:
            return page_cache[key]
        url = edhrec_commander_json_url(slug, theme_slug=theme_slug, tier_slug=tier_slug)
        response, err = _edhrec_get_with_retry(session, url, timeout=timeout)
        if err or response is None:
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

    commander_title = slug.replace("-", " ").title()
    main_hub = fetch_json("", "")
    if main_hub:
        container = main_hub.get("container") or {}
        json_dict = container.get("json_dict") or {}
        header = container.get("header") or json_dict.get("header")
        if header:
            commander_title = header.replace(" (Commander)", "").strip()

    resolved_themes: list[tuple[str, str]] = [("", "Average")]
    for th in theme_slugs:
        data = fetch_json(th, "")
        if not data:
            continue
        container = data.get("container") or {}
        resolved_themes.append((th, theme_label_from_container(container, th)))

    merged: dict[str, dict] = {}
    sources: list[str] = []

    for tier_slug, tier_label in TIER_ORDER:
        for theme_slug, theme_label in resolved_themes:
            data = fetch_json(theme_slug, tier_slug)
            label = f"{theme_label} / {tier_label}"
            if not data:
                sources.append(f"{label} (failed)")
                continue
            rows = extract_cards_with_stats(data)
            sources.append(f"{label} ({len(rows)} cardviews)")
            merge_card_stats(merged, rows)

    commander_key = commander_title.casefold()
    merged.pop(commander_key, None)

    before_count = len(merged)
    filtered = [
        entry["name"]
        for entry in merged.values()
        if passes_filter(
            entry,
            min_inclusion=min_inclusion,
            min_land_synergy=min_land_synergy,
            min_land_inclusion=min_land_inclusion,
        )
    ]
    filtered.sort(key=str.casefold)
    after_count = len(filtered)

    return filtered, commander_title, sources, before_count, after_count


def write_decklist(path: Path, cards: list[str], commander_title: str) -> None:
    lines = [f"1 {name}" for name in cards]
    if commander_title:
        lines.append(f"1 {commander_title}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def output_path_for_slug(output_dir: Path, commander_slug: str) -> Path:
    base = commander_slug.split("/")[0].strip()
    return output_dir / base


def fetch_wipcoms(
    commander_list: str | Path,
    output_dir: str | Path = "wipcoms",
    cfg: Optional[RunConfig] = None,
    *,
    max_themes: int = 5,
    min_inclusion: float = DEFAULT_MIN_INCLUSION,
    min_land_synergy: float = DEFAULT_MIN_LAND_SYNERGY,
    min_land_inclusion: float = DEFAULT_MIN_LAND_INCLUSION,
    quiet: bool = False,
) -> dict:
    """Fetch EDHREC cards for each commander into per-slug decklist files."""
    cfg = cfg or RunConfig()
    input_path = Path(commander_list)
    out_dir = Path(output_dir)

    if not input_path.is_file():
        msg = f"Error: input file not found: {input_path}"
        print(msg)
        return err_result(msg)

    names_and_types = load_commander_slugs_from_file(input_path)
    if not names_and_types:
        msg = f"Error: no commanders found in {input_path}"
        print(msg)
        return err_result(msg)

    session = create_cached_session(fresh_cache=cfg.fresh_cache)
    failures = 0
    total_before = 0
    total_after = 0
    outputs: list[str] = []

    with progress_bar(len(names_and_types), initial_value=0) as bar:
        for idx, (commander_slug, _variant) in enumerate(names_and_types):
            display = commander_slug.replace("-", " ")
            bar.update(idx, info=f"EDHREC fetch for {display}")

            cards, commander_title, sources, before_count, after_count = fetch_commander_cards(
                session,
                commander_slug,
                max_themes=max_themes if max_themes is not None else cfg.max_themes,
                min_inclusion=min_inclusion,
                min_land_synergy=min_land_synergy,
                min_land_inclusion=min_land_inclusion,
            )
            out_path = output_path_for_slug(out_dir, commander_slug)

            if not cards and not commander_title:
                print(f"Error: no cards for {commander_slug}")
                failures += 1
                continue

            write_decklist(out_path, cards, commander_title)
            outputs.append(str(out_path))
            total_before += before_count
            total_after += after_count
            print(
                f"Wrote {out_path}: {before_count} -> {after_count} cards "
                f"(>{min_inclusion:g}% inclusion / lands >{min_land_synergy:g}% syn "
                f"or >{min_land_inclusion:g}% inc) + commander"
            )
            if not quiet:
                for source in sources:
                    print(f"  {source}")

    print(
        f"\nTotal: {total_before} -> {total_after} cards across {len(names_and_types)} commanders "
        f"({total_before - total_after} removed)"
    )
    if failures:
        return err_result(
            f"{failures} commander(s) failed",
            outputs=outputs,
            error=f"{failures} failures",
        )
    return ok_result(
        f"Wrote {len(outputs)} wipcom files",
        outputs=outputs,
        before=total_before,
        after=total_after,
    )
