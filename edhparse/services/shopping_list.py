"""Shopping-list workflow: merge, drop basics, stock check, ManaPool paste export."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from utils.progress_utils import notify_progress
from edhparse.parsing import parse_deck_file
from edhparse.services import err_result, ok_result
from clients import scryfall_bulk
from clients.session import create_cached_session, ensure_scryfall_bulk
from core.config import DEFAULT_SUMMARY_BINDERS, RunConfig
from core.deck_diff import lookup_canonical, register_canonical_name
from core.stock import StockCollection

BASIC_LAND_NAMES = {
    "plains",
    "island",
    "swamp",
    "mountain",
    "forest",
    "wastes",
    "snow-covered plains",
    "snow-covered island",
    "snow-covered swamp",
    "snow-covered mountain",
    "snow-covered forest",
    "snow-covered wastes",
}


@dataclass
class ShoppingCardRow:
    name: str
    needed: int
    owned: int
    to_buy: int
    owned_by_binder: Dict[str, int] = field(default_factory=dict)


def is_basic_land_name(name: str) -> bool:
    key = (name or "").strip().casefold()
    if key in BASIC_LAND_NAMES:
        return True
    if " // " in key:
        return key.split(" // ", 1)[0].strip() in BASIC_LAND_NAMES
    return False


def is_basic_land_scryfall(data: Optional[dict], name: str) -> bool:
    if is_basic_land_name(name):
        return True
    if not data:
        return False
    type_line = str(data.get("type_line") or "")
    return "Basic Land" in type_line


def merge_shopping_list(path: Path | str) -> Counter:
    """Parse MTGO-style lines and merge duplicate card counts."""
    return Counter(parse_deck_file(path))


def allocate_ownership(
    needed: Dict[str, int],
    stock: StockCollection,
    name_to_canonical: Dict[str, str],
) -> Tuple[Dict[str, int], Dict[str, Dict[str, int]]]:
    """
    Return (owned_qty_by_canonical, owned_by_binder_by_canonical).

    Owned quantities are capped at needed amounts when allocating across binders.
    """
    binder_order, by_binder = stock.quantities_by_binder()
    remaining = Counter(
        {lookup_canonical(n, name_to_canonical): q for n, q in needed.items()}
    )
    owned_total: Counter = Counter()
    owned_by_binder: Dict[str, Dict[str, int]] = {b: {} for b in binder_order}

    for binder in binder_order:
        binder_counts = Counter()
        for raw_name, qty in by_binder.get(binder, Counter()).items():
            canon = lookup_canonical(raw_name, name_to_canonical)
            binder_counts[canon] += int(qty)

        for canon in list(remaining.keys()):
            need = remaining[canon]
            if need <= 0:
                continue
            available = binder_counts.get(canon, 0)
            if available <= 0:
                continue
            take = min(need, available)
            owned_total[canon] += take
            owned_by_binder[binder][canon] = (
                owned_by_binder[binder].get(canon, 0) + take
            )
            remaining[canon] -= take

    return dict(owned_total), owned_by_binder


def manapool_paste_text(rows: Sequence[ShoppingCardRow]) -> str:
    """MTGO-style lines ManaPool mass entry accepts: `qty Name`."""
    lines = []
    for row in sorted(rows, key=lambda r: r.name.casefold()):
        if row.to_buy > 0:
            lines.append(f"{row.to_buy} {row.name}")
    return "\n".join(lines) + ("\n" if lines else "")


def run_shopping_list(
    shopping_list: str | Path,
    stock_csv: str | Path,
    cfg: Optional[RunConfig] = None,
    *,
    binders: Optional[Sequence[str]] = None,
    out_path: str = "outputs/shopping_list.html",
) -> dict:
    """
    Build a simple owned / still-to-buy report plus a ManaPool paste list.

    - drop basic lands
    - merge duplicate lines
    - subtract Binder/Stock/secret binder ownership (partial OK)
    - write HTML summary + .txt paste file for ManaPool mass entry
    """
    cfg = cfg or RunConfig()
    list_path = Path(shopping_list)
    if not list_path.is_file():
        msg = f"Shopping list file not found: {list_path}"
        print(msg)
        return err_result(msg)

    binder_names = list(binders) if binders else list(cfg.binders or DEFAULT_SUMMARY_BINDERS)
    if not binder_names:
        binder_names = list(DEFAULT_SUMMARY_BINDERS)

    notify_progress(0, 3, "Parsing shopping list…")
    raw = merge_shopping_list(list_path)
    if not raw:
        msg = "No cards found in shopping list."
        print(msg)
        return err_result(msg)

    notify_progress(1, 3, "Loading Scryfall bulk + stock…")
    session = create_cached_session(fresh_cache=cfg.fresh_cache)
    ensure_scryfall_bulk(session)

    name_to_canonical: Dict[str, str] = {}
    merged: Counter = Counter()
    removed_basics: Counter = Counter()
    for name, qty in raw.items():
        data = scryfall_bulk.get_card_by_name_or_face(name)
        canon = (data.get("name") if data else None) or name
        register_canonical_name(name, canon, name_to_canonical)
        if is_basic_land_scryfall(data, canon):
            removed_basics[canon] += qty
            continue
        merged[canon] += qty

    stock = StockCollection.load(stock_csv, binder_names)
    if stock.error:
        print(f"Error: {stock.error}")
        return err_result(stock.error)

    for row in stock.rows:
        data = scryfall_bulk.get_card_by_name_or_face(row.name)
        canon = (data.get("name") if data else None) or row.name
        register_canonical_name(row.name, canon, name_to_canonical)

    # Stock often stores full DFC names (`Front // Back`) while lists use the front
    # face only. Re-key after stock aliases are registered so ownership lines up.
    canonical_merged: Counter = Counter()
    for name, qty in merged.items():
        canonical_merged[lookup_canonical(name, name_to_canonical)] += qty
    merged = canonical_merged

    canonical_basics: Counter = Counter()
    for name, qty in removed_basics.items():
        canonical_basics[lookup_canonical(name, name_to_canonical)] += qty
    removed_basics = canonical_basics

    owned_total, owned_by_binder = allocate_ownership(
        dict(merged), stock, name_to_canonical
    )

    rows: List[ShoppingCardRow] = []
    for name in sorted(merged.keys(), key=lambda n: n.casefold()):
        need = int(merged[name])
        owned = int(owned_total.get(name, 0))
        buy = max(0, need - owned)
        by_binder = {
            binder: qty
            for binder, mapping in owned_by_binder.items()
            if (qty := mapping.get(name, 0))
        }
        rows.append(
            ShoppingCardRow(
                name=name,
                needed=need,
                owned=owned,
                to_buy=buy,
                owned_by_binder=by_binder,
            )
        )

    notify_progress(2, 3, "Writing outputs…")
    paste = manapool_paste_text(rows)
    html_path = Path(out_path)
    if html_path.suffix.lower() != ".html":
        html_path = html_path.with_suffix(".html")
    txt_path = html_path.with_name(html_path.stem + "_manapool.txt")

    from renderers.shopping_list_renderer import write_shopping_list_html

    write_shopping_list_html(
        out_path=str(html_path),
        rows=rows,
        removed_basics=dict(removed_basics),
        stock_csv=str(stock_csv),
        binders=binder_names,
        shopping_list=str(list_path),
        manapool_paste=paste,
    )
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.write_text(paste, encoding="utf-8")

    buy_count = sum(1 for r in rows if r.to_buy > 0)
    owned_count = sum(1 for r in rows if r.to_buy == 0)
    buy_copies = sum(r.to_buy for r in rows)
    print(f"Wrote {html_path}")
    print(f"Wrote ManaPool paste list ({buy_copies} copies): {txt_path}")
    notify_progress(3, 3, "Done")
    return ok_result(
        f"Shopping list ready: {owned_count} fully owned, {buy_count} to buy "
        f"({buy_copies} copies). Paste file: {txt_path.name}",
        outputs=[str(html_path), str(txt_path)],
        buy_count=buy_count,
        owned_count=owned_count,
        basics_removed=sum(removed_basics.values()),
    )
