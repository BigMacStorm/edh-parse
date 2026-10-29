"""Run configuration shared by CLI, services, and the web UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

DEFAULT_SUMMARY_BINDERS = ("stock", "binder", "secret binder")


@dataclass
class RunConfig:
    """Runtime options previously scattered across argparse Namespace attrs."""

    fresh_cache: bool = False
    show_owned: bool = False
    need: bool = False
    show_all: bool = False
    csv_file: Optional[str] = None

    stock_csv: Optional[str] = None
    binders: List[str] = field(default_factory=lambda: list(DEFAULT_SUMMARY_BINDERS))

    # Thresholds / report knobs
    max_themes: int = 5
    safe_shopping_min_inclusion_pct: float = 20.0
    safe_shopping_min_synergy_pct: float = 20.0
    safe_shopping_min_price: float = 0.0
    safe_shopping_sort_default: str = "inclusion"
    pivot_min_price: float = 5.0
    pivot_min_inclusion_pct: float = 0.0
    pivot_top: int = 40
    compare_min_inclusion_pct: Optional[float] = None
    convert_sort: str = "price"

    def with_binders(self, binders: Optional[Sequence[str]]) -> "RunConfig":
        if binders:
            self.binders = [b for b in binders if (b or "").strip()]
        return self

    @property
    def stock_path(self) -> Optional[Path]:
        return Path(self.stock_csv) if self.stock_csv else None


# Back-compat shim: Deck/Card historically checked args.show_owned / args.need / args.all
class _ArgsShim:
    """Minimal Namespace-like object so legacy Deck/Card printing still works."""

    def __init__(self, cfg: RunConfig):
        self.show_owned = cfg.show_owned
        self.need = cfg.need
        self.all = cfg.show_all
        self.fresh_cache = cfg.fresh_cache
        self.csv_file = cfg.csv_file
