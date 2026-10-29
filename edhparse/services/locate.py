"""Locate cards in stock binders (with Scryfall rarity)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from clients import scryfall_bulk
from clients.session import create_cached_session, ensure_scryfall_bulk
from core.config import RunConfig
from core.stock import load_manabox
from edhparse.parsing import parse_card_names_file
from edhparse.services import err_result, ok_result


def locate_cards_in_stock(card_names, stock_rows, session=None):
    """
    Given a list of card names and raw stock rows from load_manabox(as_dict=False),
    group results by binder (Stock/Binder/Secret) and print cards under each,
    including rarity from Scryfall bulk data.
    """
    # Build index: lowercased card name -> binder -> total quantity
    index = {}
    for row in stock_rows:
        name = (row.get("Name") or row.get("Card") or "").strip()
        if not name:
            continue
        binder = (
            row.get("Binder Name")
            or row.get("Binder")
            or row.get("Folder")
            or ""
        ).strip()
        if not binder:
            continue
        qty_str = (
            row.get("Quantity")
            or row.get("Qty")
            or row.get("Qty.")
            or ""
        ).strip()
        try:
            qty = int(qty_str) if qty_str else 1
        except ValueError:
            qty = 1
        key = name.lower()
        if key not in index:
            index[key] = {}
        index[key][binder] = index[key].get(binder, 0) + qty

    rarity_cache = {}

    def rarity_code(card_name: str) -> str:
        if card_name in rarity_cache:
            return rarity_cache[card_name]
        data = scryfall_bulk.get_card_by_name(card_name)
        code = "?"
        if data is not None:
            r = (data.get("rarity") or "").strip().lower()
            if r:
                code = r[0].upper()
        rarity_cache[card_name] = code
        return code

    def binder_priority(name: str) -> int:
        n = (name or "").strip().lower()
        if n in ("secret binder", "secret"):
            return 0
        if n == "binder":
            return 1
        if n == "stock":
            return 2
        return 3

    binder_groups = {}
    missing = []
    for name in card_names:
        key = name.lower()
        binders = index.get(key)
        if not binders:
            missing.append(name)
            continue
        chosen_binder = None
        chosen_qty = 0
        best_prio = 10
        for binder, qty in binders.items():
            pr = binder_priority(binder)
            if pr < best_prio:
                best_prio = pr
                chosen_binder = binder
                chosen_qty = qty
        if chosen_binder is None:
            missing.append(name)
            continue
        rcode = rarity_code(name)
        binder_groups.setdefault(chosen_binder, []).append((name, chosen_qty, rcode))

    for binder in sorted(binder_groups.keys(), key=binder_priority):
        print(binder)
        for name, qty, rcode in sorted(binder_groups[binder], key=lambda t: t[0].lower()):
            print(f"  {qty}x {name} [{rcode}]")

    if missing:
        print("\nNot found in Stock/Binder/Secret binders:")
        for name in missing:
            print(f"  {name}")

    return binder_groups, missing


def run_locate(
    locate_path: str | Path,
    stock_csv: str | Path,
    cfg: Optional[RunConfig] = None,
) -> dict:
    """Locate cards from a names file in stock binders."""
    cfg = cfg or RunConfig()
    locate_file = Path(locate_path)
    if not locate_file.is_file():
        msg = f"Error: --locate argument '{locate_path}' is not a valid file."
        print(msg)
        return err_result(msg)

    card_names = parse_card_names_file(locate_file)
    if not card_names:
        msg = "No card names found to locate."
        print(msg)
        return err_result(msg)

    stock_rows = load_manabox(stock_csv)
    if stock_rows is None:
        return err_result("Failed to load stock CSV.")

    session = create_cached_session(fresh_cache=cfg.fresh_cache)
    ensure_scryfall_bulk(session)

    groups, missing = locate_cards_in_stock(card_names, stock_rows, session)
    found = sum(len(v) for v in groups.values())
    return ok_result(
        f"Located {found} cards; {len(missing)} missing",
        found=found,
        missing=missing,
    )
