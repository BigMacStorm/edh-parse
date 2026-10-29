"""ManaPool public price feed (cheapest NM+ printing lookup)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import requests

MANAPOOL_PRICES_URL = "https://manapool.com/api/v1/prices/singles"
DEFAULT_CACHE_PATH = Path("cache/manapool_singles_prices.json")
DEFAULT_MAX_AGE_SECONDS = 60 * 60 * 12  # 12 hours


@dataclass
class ManaPoolOffer:
    name: str
    price_usd: float
    set_code: str = ""
    number: str = ""
    url: str = ""
    finish: str = "nonfoil"  # nonfoil | foil | etched
    available_quantity: int = 0


def _nm_candidates(row: dict) -> List[Tuple[float, str]]:
    """Return (price_usd, finish) options that are Near Mint."""
    out: List[Tuple[float, str]] = []
    mapping = (
        ("price_cents_nm", "nonfoil"),
        ("price_cents_nm_foil", "foil"),
        ("price_cents_nm_etched", "etched"),
    )
    for key, finish in mapping:
        cents = row.get(key)
        if cents is None:
            continue
        try:
            price = float(cents) / 100.0
        except (TypeError, ValueError):
            continue
        if price < 0:
            continue
        out.append((price, finish))
    return out


def _normalize_name(name: str) -> str:
    return (name or "").strip().casefold()


class ManaPoolPriceIndex:
    """In-memory index of cheapest NM ManaPool printing per card name."""

    def __init__(self, by_name: Dict[str, ManaPoolOffer]):
        self.by_name = by_name

    @classmethod
    def from_rows(cls, rows: Iterable[dict]) -> "ManaPoolPriceIndex":
        best: Dict[str, ManaPoolOffer] = {}
        for row in rows:
            name = (row.get("name") or "").strip()
            if not name:
                continue
            qty = int(row.get("available_quantity") or 0)
            if qty <= 0:
                continue
            for price, finish in _nm_candidates(row):
                offer = ManaPoolOffer(
                    name=name,
                    price_usd=price,
                    set_code=(row.get("set_code") or "").strip(),
                    number=str(row.get("number") or "").strip(),
                    url=(row.get("url") or "").strip(),
                    finish=finish,
                    available_quantity=qty,
                )
                key = _normalize_name(name)
                prev = best.get(key)
                if prev is None or offer.price_usd < prev.price_usd:
                    best[key] = offer
                # Also index front face for DFCs
                if " // " in name:
                    front = name.split(" // ", 1)[0].strip()
                    fkey = _normalize_name(front)
                    prev_f = best.get(fkey)
                    if prev_f is None or offer.price_usd < prev_f.price_usd:
                        best[fkey] = offer
        return cls(best)

    def lookup(self, name: str) -> Optional[ManaPoolOffer]:
        key = _normalize_name(name)
        hit = self.by_name.get(key)
        if hit:
            return hit
        if " // " in name:
            return self.by_name.get(_normalize_name(name.split(" // ", 1)[0]))
        return None


def _cache_is_fresh(path: Path, max_age_seconds: int) -> bool:
    if not path.is_file():
        return False
    age = time.time() - path.stat().st_mtime
    return age <= max_age_seconds


def load_price_index(
    session: Optional[requests.Session] = None,
    *,
    cache_path: Path | str = DEFAULT_CACHE_PATH,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    force_refresh: bool = False,
) -> ManaPoolPriceIndex:
    """Load ManaPool singles prices from cache or download the public feed."""
    path = Path(cache_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = None
    if not force_refresh and _cache_is_fresh(path, max_age_seconds):
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            payload = None

    if payload is None:
        sess = session or requests.Session()
        sess.headers.setdefault("User-Agent", "edhparse/2.0 (+local shopping tool)")
        sess.headers.setdefault("Accept", "application/json")
        print("Downloading ManaPool singles price feed...", flush=True)
        response = sess.get(MANAPOOL_PRICES_URL, timeout=180)
        response.raise_for_status()
        payload = response.json()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f)

    rows = payload.get("data") or []
    return ManaPoolPriceIndex.from_rows(rows)
