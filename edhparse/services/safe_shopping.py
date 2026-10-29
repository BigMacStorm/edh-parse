"""Safe shopping HTML: EDHREC inclusion/synergy shopping page per commander."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional, Sequence, Set

from clients.session import create_cached_session
from core.card import Card
from core.config import RunConfig, _ArgsShim
from core.deck import EDHREC_SLEEP, _edhrec_get_with_retry
from core.stock import StockCollection
from edhparse.edhrec_util import inclusion_pct, synergy_pct
from edhparse.parsing import load_commander_slugs_from_file
from edhparse.services import err_result, ok_result
from utils.progress_utils import progress_bar


def load_owned_names_from_stock_csv(filename, binder_names) -> Optional[Set[str]]:
    """Return a set of owned card names (lowercased) within selected binders."""
    stock = StockCollection.load(filename, binder_names)
    if stock.error:
        print(f"Error: {stock.error}")
        return None
    return {r.name.lower() for r in stock.rows if r.name}


# Back-compat alias
_load_owned_names_from_stock_csv = load_owned_names_from_stock_csv


def run_safe_shopping(
    commander_list: str | Path,
    out_path: str,
    cfg: RunConfig,
    stock_csv: Optional[str] = None,
    binders: Optional[Sequence[str]] = None,
) -> dict:
    """
    Build a commander-grouped shopping page from average EDHREC lists.
    All qualifying cards are loaded once; threshold filtering happens client-side in HTML.
    """
    names_and_types = load_commander_slugs_from_file(commander_list)
    if not names_and_types:
        msg = "No commanders found in commander_list."
        print(msg)
        return err_result(msg)

    inclusion_cutoff = float(cfg.safe_shopping_min_inclusion_pct or 0.0)
    synergy_cutoff = float(cfg.safe_shopping_min_synergy_pct or 0.0)
    min_price = float(cfg.safe_shopping_min_price or 0.0)
    owned_names: Set[str] = set()
    if stock_csv:
        owned_names = load_owned_names_from_stock_csv(stock_csv, binders or [])
        if owned_names is None:
            return err_result("Failed to load owned names from stock CSV.")

    session = create_cached_session(fresh_cache=cfg.fresh_cache)
    args = _ArgsShim(cfg)

    card_cache = {}
    output_rows = []
    tier_suffixes = [("", "average")]

    with progress_bar(len(names_and_types), initial_value=0) as commander_bar:
        idx = 0
        for commander_slug, _variant in names_and_types:
            commander_bar.update(idx, info=f"Safe shopping lookup for {commander_slug.replace('-', ' ')}")
            idx += 1

            merged = {}
            commander_title = commander_slug.replace("-", " ").title()

            for suffix, tier_label in tier_suffixes:
                url = f"https://json.edhrec.com/pages/commanders/{commander_slug}{suffix}.json"
                response, err = _edhrec_get_with_retry(session, url, timeout=10)
                if err:
                    continue
                if not getattr(response, "from_cache", True):
                    time.sleep(EDHREC_SLEEP)
                try:
                    data = response.json()
                except Exception:
                    continue

                container = data.get("container") or {}
                json_dict = container.get("json_dict") or {}
                cardlists = json_dict.get("cardlists") or []
                header = container.get("header") or json_dict.get("header")
                if header:
                    commander_title = header.replace(" (Commander)", "").strip()

                for section in cardlists:
                    for cv in section.get("cardviews") or []:
                        name = (cv.get("name") or "").strip()
                        if not name:
                            continue
                        inc_pct = inclusion_pct(cv)
                        syn_pct = synergy_pct(cv)

                        if inc_pct is None:
                            continue
                        cur = merged.get(name)
                        if cur is None or inc_pct > cur["inclusion_pct"]:
                            merged[name] = {
                                "name": name,
                                "inclusion_pct": inc_pct,
                                "inclusion_label": f"{inc_pct:.1f}%",
                                "synergy_pct": syn_pct,
                                "synergy_label": f"{syn_pct:.1f}%" if syn_pct is not None else "—",
                                "tiers": {tier_label},
                            }
                        else:
                            cur["tiers"].add(tier_label)

            if not merged:
                output_rows.append(
                    {
                        "slug": commander_slug,
                        "name": commander_title,
                        "cards": [],
                        "owned_pct": 0.0,
                        "owned_count": 0,
                        "card_count": 0,
                    }
                )
                continue

            cards = []
            for name in sorted(merged.keys(), key=str.lower):
                row = merged[name]
                key = name.lower()
                cached = card_cache.get(key)
                if cached is None:
                    c = Card(session, args, is_commander=False)
                    c.search_card(name)
                    if c.error:
                        cached = {
                            "price": None,
                            "type_line": "",
                            "set_name": "",
                            "set_code": "",
                            "card_pic": "",
                        }
                    else:
                        cached = {
                            "price": c.price,
                            "type_line": c.type_line or "",
                            "set_name": c.set_name or "",
                            "set_code": c.set_code or "",
                            "card_pic": c.card_pic or "",
                        }
                    card_cache[key] = cached

                type_line = (cached.get("type_line") or "").lower()
                if "land" in type_line:
                    continue

                owned = key in owned_names if owned_names else False
                cards.append(
                    {
                        "name": name,
                        "inclusion_pct": row["inclusion_pct"],
                        "inclusion_label": row["inclusion_label"],
                        "synergy_pct": row["synergy_pct"],
                        "synergy_label": row["synergy_label"],
                        "tiers": sorted(list(row["tiers"])),
                        "owned": owned,
                        "price": cached["price"],
                        "type_line": cached["type_line"],
                        "set_name": cached["set_name"],
                        "set_code": cached["set_code"],
                        "card_pic": cached["card_pic"],
                    }
                )

            owned_count = sum(1 for c in cards if c["owned"])
            card_count = len(cards)
            owned_pct = (100.0 * owned_count / card_count) if card_count else 0.0
            output_rows.append(
                {
                    "slug": commander_slug,
                    "name": commander_title,
                    "cards": cards,
                    "owned_pct": owned_pct,
                    "owned_count": owned_count,
                    "card_count": card_count,
                }
            )

    resolved_out = out_path or "outputs/safe_shopping.html"
    from renderers.safe_shopping_renderer import write_safe_shopping_html

    write_safe_shopping_html(
        out_path=resolved_out,
        rows=output_rows,
        inclusion_cutoff=inclusion_cutoff,
        synergy_cutoff=synergy_cutoff,
        min_price=min_price,
        default_sort=cfg.safe_shopping_sort_default or "inclusion",
        has_ownership_data=bool(stock_csv),
        stock_csv=stock_csv or "",
        binder_names=list(binders or []),
    )

    return ok_result(f"Wrote safe shopping to {resolved_out}", outputs=[resolved_out])
