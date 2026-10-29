"""Shared EDHREC slugify / inclusion / synergy helpers."""

from __future__ import annotations

import re
from typing import Optional


def edhrec_name_slug(commander_name: str) -> str:
    """Slugify a commander display name for EDHREC paths (letters/digits/spaces/hyphens)."""
    stripped = re.sub(r"[^\w\s\-]", "", commander_name or "")
    return stripped.lower().replace(" ", "-")


def edhrec_card_slug(name: str) -> str:
    """Normalize a card name into an EDHREC /cards/ slug (front face only for DFCs)."""
    text = (name or "").strip().lower()
    if " // " in text:
        text = text.split(" // ", 1)[0].strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text


def inclusion_pct(cv: dict) -> Optional[float]:
    """Deck inclusion as a percentage 0-100, or None if not derivable."""
    nd = cv.get("num_decks")
    pd = cv.get("potential_decks")
    try:
        nd_f = float(nd)
        pd_f = float(pd)
        if pd_f > 0:
            return 100.0 * nd_f / pd_f
    except (TypeError, ValueError):
        pass

    p = cv.get("p")
    if p is not None:
        try:
            v = float(p)
        except (TypeError, ValueError):
            return None
        if -2.0 <= v <= 2.0:
            v *= 100.0
        return v
    return None


def synergy_pct(cv: dict) -> Optional[float]:
    """Synergy as a percentage 0-100, or None."""
    for key in ("synergy", "s"):
        raw = cv.get(key)
        if raw is None:
            continue
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        if -2.0 <= v <= 2.0:
            v *= 100.0
        return v
    return None


def format_pct(val: Optional[float], digits: int = 1) -> str:
    if val is None:
        return "—"
    return f"{val:.{digits}f}%"
