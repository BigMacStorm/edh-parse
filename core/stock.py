"""Unified ManaBox / stock CSV ownership loading."""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from core.config import DEFAULT_SUMMARY_BINDERS


def find_csv_col(header_row: Sequence[str], *candidates: str) -> Optional[int]:
    """Return index of first column whose name (stripped, lower) matches a candidate."""
    norm = {i: (h or "").strip().lower() for i, h in enumerate(header_row)}
    for want in candidates:
        want_l = want.strip().lower()
        for i, name in norm.items():
            if name == want_l:
                return i
    return None


@dataclass
class StockRow:
    name: str
    binder: str
    quantity: int = 1
    set_code: str = ""
    collector_number: str = ""
    scryfall_id: str = ""
    raw: Dict[str, str] = field(default_factory=dict)


@dataclass
class StockCollection:
    """Cards owned in selected binders from a ManaBox-style export."""

    path: Path
    binders: List[str]
    rows: List[StockRow] = field(default_factory=list)
    error: Optional[str] = None

    @classmethod
    def load(
        cls,
        filename: str | Path,
        binder_names: Optional[Sequence[str]] = None,
        *,
        default_binders: Sequence[str] = DEFAULT_SUMMARY_BINDERS,
    ) -> "StockCollection":
        path = Path(filename)
        binders = [b.strip() for b in (binder_names or default_binders) if (b or "").strip()]
        if not binders:
            return cls(path=path, binders=[], rows=[], error="No binder names were provided.")
        if not path.is_file():
            return cls(path=path, binders=binders, rows=[], error=f"File not found at {filename}")

        wanted = {b.lower() for b in binders}
        try:
            with open(path, "r", encoding="utf-8") as csvfile:
                reader = csv.reader(csvfile, delimiter=",", quotechar='"')
                header_row = next(reader, None)
                if not header_row:
                    return cls(path=path, binders=binders, rows=[])

                name_col = find_csv_col(header_row, "Name", "Card")
                qty_col = find_csv_col(header_row, "Quantity", "Qty", "Qty.")
                binder_col = find_csv_col(header_row, "Binder Name", "Binder", "Folder")
                set_col = find_csv_col(header_row, "Set code", "Set", "Set Code")
                collector_col = find_csv_col(header_row, "Collector number", "Collector Number", "Number")
                scryfall_id_col = find_csv_col(header_row, "Scryfall ID", "Scryfall Id")
                if name_col is None or binder_col is None:
                    return cls(
                        path=path,
                        binders=binders,
                        rows=[],
                        error=(
                            "CSV must have a card column ('Name' or 'Card') and a binder column "
                            "('Binder Name', 'Binder', or 'Folder')."
                        ),
                    )

                binder_header = header_row[binder_col] if binder_col < len(header_row) else ""
                out: List[StockRow] = []
                for row in reader:
                    row_data = {}
                    for i, cell in enumerate(row):
                        if i < len(header_row):
                            row_data[header_row[i]] = cell
                    binder_val = (row_data.get(binder_header) or "").strip()
                    if binder_val.lower() not in wanted:
                        continue
                    card_name = (row_data.get(header_row[name_col]) or "").strip()
                    if not card_name:
                        continue
                    qty = 1
                    if qty_col is not None and qty_col < len(row):
                        raw_qty = (row[qty_col] or "").strip()
                        if raw_qty.isdigit():
                            qty = int(raw_qty)
                    out.append(
                        StockRow(
                            name=card_name,
                            binder=binder_val,
                            quantity=qty,
                            set_code=(row[set_col].strip() if set_col is not None and set_col < len(row) else ""),
                            collector_number=(
                                row[collector_col].strip()
                                if collector_col is not None and collector_col < len(row)
                                else ""
                            ),
                            scryfall_id=(
                                row[scryfall_id_col].strip()
                                if scryfall_id_col is not None and scryfall_id_col < len(row)
                                else ""
                            ),
                            raw=row_data,
                        )
                    )
                return cls(path=path, binders=binders, rows=out)
        except Exception as e:
            return cls(path=path, binders=binders, rows=[], error=f"Error reading stock CSV: {e}")

    @classmethod
    def list_binder_names(cls, filename: str | Path) -> List[str]:
        """Return unique binder names found in a CSV (display order, first-seen)."""
        path = Path(filename)
        if not path.is_file():
            return []
        try:
            with open(path, "r", encoding="utf-8") as csvfile:
                reader = csv.reader(csvfile, delimiter=",", quotechar='"')
                header_row = next(reader, None)
                if not header_row:
                    return []
                binder_col = find_csv_col(header_row, "Binder Name", "Binder", "Folder")
                if binder_col is None:
                    return []
                seen = set()
                ordered: List[str] = []
                for row in reader:
                    if binder_col >= len(row):
                        continue
                    name = (row[binder_col] or "").strip()
                    key = name.lower()
                    if not name or key in seen:
                        continue
                    seen.add(key)
                    ordered.append(name)
                return ordered
        except Exception:
            return []

    def as_name_set(self) -> set:
        return {r.name for r in self.rows if r.name}

    def as_manabox_rows(self) -> List[Dict[str, str]]:
        """Dict rows compatible with Collection.mark_cards_owned (Name/Card keys)."""
        out = []
        for r in self.rows:
            d = dict(r.raw) if r.raw else {"Name": r.name, "Binder Name": r.binder}
            if "Name" not in d and "Card" not in d:
                d["Name"] = r.name
            out.append(d)
        return out

    def quantities(self) -> Dict[str, int]:
        totals: Counter = Counter()
        for r in self.rows:
            totals[r.name] += int(r.quantity or 1)
        return dict(totals)

    def quantities_by_binder(self) -> Tuple[List[str], Dict[str, Counter]]:
        """Preserve requested binder display order; append extras found in CSV."""
        binder_display: Dict[str, str] = {}
        binder_order: List[str] = []
        for name in self.binders:
            key = name.strip().lower()
            if key and key not in binder_display:
                binder_display[key] = name.strip()
                binder_order.append(name.strip())

        by_binder: Dict[str, Counter] = {name: Counter() for name in binder_order}
        for r in self.rows:
            key = r.binder.strip().lower()
            display = binder_display.get(key) or r.binder.strip()
            if display not in by_binder:
                by_binder[display] = Counter()
                if display not in binder_order:
                    binder_order.append(display)
            by_binder[display][r.name] += int(r.quantity or 1)
        return binder_order, by_binder


# Back-compat wrappers used by older tests / call sites
def load_owned_quantities_by_binder_from_stock_csv(filename, binder_names):
    stock = StockCollection.load(filename, binder_names)
    if stock.error:
        print(f"Error: {stock.error}")
        return None
    return stock.quantities_by_binder()


def load_owned_quantities_from_stock_csv(filename, binder_names):
    loaded = load_owned_quantities_by_binder_from_stock_csv(filename, binder_names)
    if loaded is None:
        return None
    _, by_binder = loaded
    quantities: Counter = Counter()
    for binder_counts in by_binder.values():
        quantities.update(binder_counts)
    return dict(quantities)


def load_stock_rows_for_binders(filename, binder_names) -> Optional[List[dict]]:
    stock = StockCollection.load(filename, binder_names)
    if stock.error:
        print(f"Error: {stock.error}")
        return None
    return [
        {
            "name": r.name,
            "binder": r.binder,
            "quantity": r.quantity,
            "set_code": r.set_code,
            "collector_number": r.collector_number,
            "scryfall_id": r.scryfall_id,
        }
        for r in stock.rows
    ]


def load_manabox(filename, as_dict=False, binder_names: Optional[Sequence[str]] = None):
    """Load stock/Manabox CSV filtered to default (or provided) binders."""
    stock = StockCollection.load(filename, binder_names)
    if stock.error:
        print(f"Error: {stock.error}")
        return None
    if as_dict:
        return stock.quantities()
    return stock.as_manabox_rows()
