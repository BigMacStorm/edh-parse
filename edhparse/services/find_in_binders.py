"""Find pasted card names in stock binders."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from clients import scryfall_bulk
from clients.session import create_cached_session
from core.config import DEFAULT_SUMMARY_BINDERS, RunConfig
from core.stock import StockCollection
from edhparse.parsing import parse_card_counts_file, parse_card_counts_lines
from edhparse.services import err_result, ok_result

# Exact binder names in inputs/Stock.csv (matched case-insensitively when loading).
FIND_BINDERS = DEFAULT_SUMMARY_BINDERS  # stock, binder, secret binder
RARITY_BUCKETS = {
    "mythic": "Rares",
    "rare": "Rares",
    "uncommon": "Uncommons",
    "common": "Commons",
}
COLOR_BUCKET_ORDER = ["Colorless", "White", "Blue", "Black", "Red", "Green", "Multicolor"]


@dataclass
class BinderHit:
    name: str
    needed: int
    owned: int
    binders: List[Tuple[str, int]] = field(default_factory=list)
    rarity_bucket: str = "Other"
    color_bucket: str = "Colorless"

    @property
    def found(self) -> bool:
        return self.owned > 0


def _binder_index(stock: StockCollection) -> Dict[str, Dict[str, int]]:
    """Lowercased card name -> {binder display name -> total qty}."""
    index: Dict[str, Dict[str, int]] = {}

    def add_name(name: str, binder: str, qty: int) -> None:
        key = name.strip().lower()
        if not key:
            return
        index.setdefault(key, {})
        index[key][binder] = index[key].get(binder, 0) + qty

    for row in stock.rows:
        binder = row.binder.strip()
        if not binder:
            continue
        qty = int(row.quantity or 1)
        add_name(row.name, binder, qty)

        # Also index each face name so pasted DFC/MDFC face names resolve to owned full-name rows.
        data = scryfall_bulk.get_card_by_name_or_face(row.name)
        if not data:
            continue
        for face in data.get("card_faces") or []:
            face_name = (face.get("name") or "").strip()
            if face_name:
                add_name(face_name, binder, qty)
    return index


def _binder_section_order(name: str) -> int:
    n = (name or "").strip().lower()
    if n == "stock":
        return 0
    if n == "binder":
        return 1
    if n in ("secret binder", "secret"):
        return 2
    return 3


def _canonical_binder_order(stock: StockCollection) -> List[str]:
    """Return CSV display labels for Stock / Binder / secret binder in walk order."""
    wanted = [b.lower() for b in FIND_BINDERS]
    labels: Dict[str, str] = {}
    for row in stock.rows:
        key = row.binder.strip().lower()
        if key in wanted and key not in labels:
            labels[key] = row.binder.strip()
    return [labels[k] for k in wanted if k in labels]


def _rarity_bucket(card_name: str) -> str:
    data = scryfall_bulk.get_card_by_name_or_face(card_name)
    rarity = ((data or {}).get("rarity") or "").strip().lower()
    return RARITY_BUCKETS.get(rarity, "Other")


def _color_bucket(card_name: str) -> str:
    data = scryfall_bulk.get_card_by_name_or_face(card_name) or {}
    colors = list(data.get("color_identity") or [])
    if not colors:
        return "Colorless"
    unique = sorted({str(c).upper() for c in colors if c})
    if len(unique) >= 2:
        return "Multicolor"
    mapping = {
        "W": "White",
        "U": "Blue",
        "B": "Black",
        "R": "Red",
        "G": "Green",
    }
    return mapping.get(unique[0], "Colorless")


def _bucket_sort_key(hit: BinderHit) -> Tuple[int, int, str]:
    rarity_order = {"Rares": 0, "Uncommons": 1, "Commons": 2, "Other": 3}
    return (
        rarity_order.get(hit.rarity_bucket, 99),
        COLOR_BUCKET_ORDER.index(hit.color_bucket) if hit.color_bucket in COLOR_BUCKET_ORDER else 99,
        hit.name.casefold(),
    )


def find_cards_in_binders(
    card_counts: Dict[str, int],
    stock: StockCollection,
) -> List[BinderHit]:
    """Return each requested card with needed/owned counts and storage buckets."""
    index = _binder_index(stock)
    results: List[BinderHit] = []
    for name, needed in card_counts.items():
        binders = index.get(name.strip().lower()) or {}
        locations = sorted(binders.items(), key=lambda t: (_binder_section_order(t[0]), t[0].casefold()))
        owned = sum(qty for _binder, qty in locations)
        results.append(
            BinderHit(
                name=name,
                needed=needed,
                owned=owned,
                binders=locations,
                rarity_bucket=_rarity_bucket(name),
                color_bucket=_color_bucket(name),
            )
        )
    results.sort(key=_bucket_sort_key)
    return results


def group_hits_by_rarity_and_color(
    hits: Sequence[BinderHit],
) -> Tuple[Dict[str, Dict[str, List[BinderHit]]], List[BinderHit]]:
    """Group cards by storage layout: rarity, then color bucket."""
    groups: Dict[str, Dict[str, List[BinderHit]]] = {}
    missing: List[BinderHit] = []
    for hit in hits:
        if not hit.found:
            missing.append(hit)
        groups.setdefault(hit.rarity_bucket, {})
        groups[hit.rarity_bucket].setdefault(hit.color_bucket, [])
        groups[hit.rarity_bucket][hit.color_bucket].append(hit)
    return groups, missing


def run_find_in_binders(
    *,
    card_names: Optional[Sequence[str]] = None,
    names_file: Optional[str | Path] = None,
    card_list_text: Optional[str] = None,
    stock_csv: str | Path,
    binder_names: Optional[Sequence[str]] = None,
    out_path: str = "outputs/find_in_binders.html",
    cfg: Optional[RunConfig] = None,
) -> dict:
    """Locate pasted or file-based card names across Stock / Binder / secret binder."""
    _ = cfg  # reserved for future options (e.g. fresh_cache)
    counts: Counter = Counter()
    if card_names:
        for name in card_names:
            if name and str(name).strip():
                counts[str(name).strip()] += 1
    if card_list_text and card_list_text.strip():
        counts.update(parse_card_counts_lines(card_list_text.splitlines()))
    if names_file:
        path = Path(names_file)
        if not path.is_file():
            msg = f"Card names file not found: {names_file}"
            print(msg)
            return err_result(msg)
        counts.update(parse_card_counts_file(path))

    ordered_counts: Dict[str, int] = {}
    for name, qty in counts.items():
        clean = name.strip()
        if not clean:
            continue
        ordered_counts[clean] = ordered_counts.get(clean, 0) + int(qty or 0)

    if not ordered_counts:
        msg = "No card names provided."
        print(msg)
        return err_result(msg)

    stock_path = Path(stock_csv)
    if not stock_path.is_file():
        msg = f"Stock CSV not found: {stock_csv}"
        print(msg)
        return err_result(msg)

    binders = list(binder_names) if binder_names else list(FIND_BINDERS)
    stock = StockCollection.load(stock_path, binders)
    if stock.error:
        print(f"Error: {stock.error}")
        return err_result(stock.error)

    binder_order = _canonical_binder_order(stock)
    if not binder_order:
        msg = "None of Stock / Binder / secret binder were found in the stock CSV."
        print(msg)
        return err_result(msg)

    session = create_cached_session()
    scryfall_bulk.ensure_loaded(session)

    hits = find_cards_in_binders(ordered_counts, stock)
    groups, missing = group_hits_by_rarity_and_color(hits)
    found_count = len([hit for hit in hits if hit.found])

    rarity_order = ["Rares", "Uncommons", "Commons", "Other"]
    for rarity in rarity_order:
        color_groups = groups.get(rarity) or {}
        if not any(color_groups.get(color) for color in COLOR_BUCKET_ORDER):
            continue
        print(rarity)
        for color in COLOR_BUCKET_ORDER:
            cards = color_groups.get(color) or []
            if not cards:
                continue
            print(f"  {color}")
            for hit in cards:
                binder_bits = ", ".join(f"{binder} x{qty}" for binder, qty in hit.binders)
                print(f"    {hit.name} ({hit.needed}/{hit.owned}) [{binder_bits}]")

    if missing:
        print("\nNot found in Stock / Binder / secret binder:")
        for hit in missing:
            print(f"  {hit.name} ({hit.needed}/0)")

    resolved_out = out_path or "outputs/find_in_binders.html"
    from renderers.find_in_binders_renderer import write_find_in_binders_html

    write_find_in_binders_html(
        out_path=resolved_out,
        groups=groups,
        missing=missing,
        stock_csv=str(stock_path),
        binders=binder_order,
        requested=sum(ordered_counts.values()),
        found=found_count,
    )

    return ok_result(
        f"Found {found_count} of {len(ordered_counts)} unique cards in Stock / Binder / secret binder",
        outputs=[resolved_out],
        found=found_count,
        missing=len(missing),
    )
