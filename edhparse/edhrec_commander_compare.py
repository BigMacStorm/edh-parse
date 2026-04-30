"""
Compare two EDHREC commander pages (base or themed) and report card overlap with inclusion stats.
"""

from __future__ import annotations

import re
import time
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse

from core.deck import EDHREC_SLEEP, _edhrec_get_with_retry


def parse_edhrec_commander_slug(raw: str) -> str:
    """
    Normalize a user-provided EDHREC commander URL or path into the json.edhrec.com slug
    (e.g. 'toph-earthbending-master' or 'toph-earthbending-master/landfall').

    Strips a trailing /budget or /expensive segment when it denotes EDHREC's price variant
    (those tiers are merged separately when fetching).
    """
    text = (raw or "").strip()
    if not text:
        raise ValueError("Empty commander URL or slug.")

    lower = text.lower()
    if "/commanders/" in lower:
        if not re.match(r"^https?://", text, re.I):
            text = "https://" + text.lstrip("/")
        parsed = urlparse(text)
        path = (parsed.path or "").strip().lower()
        if not path.startswith("/"):
            path = "/" + path
        m = re.search(r"/commanders/([^?#]+)", path, re.I)
        if not m:
            raise ValueError(f"Could not parse EDHREC commander path from: {raw!r}")
        slug = m.group(1).strip("/")
    else:
        slug = text.split("#", 1)[0].split("?", 1)[0].strip("/")
        if not slug or ".." in slug:
            raise ValueError(f"Could not parse EDHREC commander slug from: {raw!r}")

    parts = [p for p in slug.split("/") if p]
    if parts and parts[-1] in ("budget", "expensive"):
        parts = parts[:-1]
    if not parts:
        raise ValueError(f"No commander slug after /commanders/ in: {raw!r}")
    return "/".join(parts)


def _fmt_pct_from_p(val) -> Optional[str]:
    if val is None:
        return None
    try:
        v = float(val)
    except (TypeError, ValueError):
        return None
    if -2.0 <= v <= 2.0:
        v *= 100.0
    return f"{v:.1f}%"


def inclusion_label(cv: dict) -> str:
    """Human-readable inclusion for one EDHREC cardview."""
    pct = inclusion_pct(cv)
    if pct is not None:
        return f"{pct:.1f}%"

    legacy = _fmt_pct_from_p(cv.get("p"))
    if legacy:
        return legacy

    inc = cv.get("inclusion")
    if inc is not None:
        return str(inc)
    return "—"


def inclusion_stat_display(cv: dict) -> Optional[str]:
    """Like inclusion_label but None when inclusion cannot be derived (for optional UI)."""
    lab = inclusion_label(cv)
    return None if lab == "—" else lab


def inclusion_pct(cv: dict) -> Optional[float]:
    """Deck inclusion as a percentage 0-100, or None if not derivable from this cardview.

    When ``num_decks`` / ``potential_decks`` are present, they are relative to the EDHREC
    page that produced this cardview (average vs ``/budget`` vs ``/expensive`` cohorts),
    not interchangeable across those pages.
    """
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


def fetch_commander_cards_inclusions(session, slug: str) -> Tuple[Dict[str, Tuple[Optional[float], str]], Optional[str]]:
    """
    Union of card names across EDHREC Normal, Budget, and Expensive commander JSON.

    Inclusion numbers always come from the **average** (no suffix) page so they match
    edhrec.com's main commander view. Cards that only appear on budget/expensive lists
    get ``(None, "—")`` for inclusion here.
    """
    primary_stats: Dict[str, Tuple[Optional[float], str]] = {}
    union_order: list[str] = []
    union_seen: set[str] = set()
    title: Optional[str] = None

    for suffix in ("", "/budget", "/expensive"):
        url = f"https://json.edhrec.com/pages/commanders/{slug}{suffix}.json"
        response, err = _edhrec_get_with_retry(session, url, timeout=10)
        if err or response is None:
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

        if title is None:
            header = container.get("header") or json_dict.get("header")
            if header:
                title = header.replace(" (Commander)", "").strip()

        is_primary = suffix == ""
        for section in cardlists:
            for cv in section.get("cardviews") or []:
                name = (cv.get("name") or "").strip()
                if not name:
                    continue
                if is_primary and name not in primary_stats:
                    primary_stats[name] = (inclusion_pct(cv), inclusion_label(cv))
                if name not in union_seen:
                    union_seen.add(name)
                    union_order.append(name)

    return {name: primary_stats.get(name, (None, "—")) for name in union_order}, title


def dice_similarity_pct(set_a, set_b) -> float:
    """Sørensen–Dice as a percentage: 100 * 2 * |A∩B| / (|A|+|B|)."""
    la, lb = len(set_a), len(set_b)
    if la == 0 and lb == 0:
        return 0.0
    inter = len(set_a & set_b)
    return round(100.0 * 2.0 * inter / (la + lb), 1)


def mean_overlap_pct(set_a, set_b) -> float:
    """
    Average of 'what fraction of this page's cards also appears on the other page'
    for both sides. For two 100-card lists with 30 shared cards, this is 30.0.
    """
    la, lb = len(set_a), len(set_b)
    if la == 0 and lb == 0:
        return 0.0
    inter = len(set_a & set_b)
    parts = []
    if la > 0:
        parts.append(inter / la)
    if lb > 0:
        parts.append(inter / lb)
    return round(100.0 * sum(parts) / len(parts), 1) if parts else 0.0


def run_compare_edhrec_commanders(
    session,
    url_a: str,
    url_b: str,
    *,
    min_inclusion_both_pct: Optional[float] = None,
) -> None:
    slug_a = parse_edhrec_commander_slug(url_a)
    slug_b = parse_edhrec_commander_slug(url_b)

    print(f"Loading EDHREC cards for: {slug_a}")
    cards_a, title_a = fetch_commander_cards_inclusions(session, slug_a)
    if not cards_a:
        print(f"Error: No cards found for commander page {slug_a!r}. Check the URL or try --fresh-cache.")
        return

    print(f"Loading EDHREC cards for: {slug_b}")
    cards_b, title_b = fetch_commander_cards_inclusions(session, slug_b)
    if not cards_b:
        print(f"Error: No cards found for commander page {slug_b!r}. Check the URL or try --fresh-cache.")
        return

    names_a = set(cards_a.keys())
    names_b = set(cards_b.keys())
    both = names_a & names_b

    label_a = title_a or slug_a.replace("-", " ").replace("/", " / ")
    label_b = title_b or slug_b.replace("-", " ").replace("/", " / ")

    table_names = both
    if min_inclusion_both_pct is not None:
        thr = float(min_inclusion_both_pct)
        filtered = set()
        for name in both:
            pa, la = cards_a.get(name, (None, "—"))
            pb, lb = cards_b.get(name, (None, "—"))
            if pa is not None and pb is not None and pa > thr and pb > thr:
                filtered.add(name)
        table_names = filtered

    print()
    print(f"A: {label_a} ({len(names_a)} cards)")
    print(f"B: {label_b} ({len(names_b)} cards)")
    print(f"Cards in both: {len(both)}")
    if min_inclusion_both_pct is not None:
        print(
            f"Showing overlap where inclusion > {min_inclusion_both_pct:g}% on both pages "
            f"({len(table_names)} cards; rows without a numeric % on either page are omitted)"
        )
    mean_ov = mean_overlap_pct(names_a, names_b)
    print(f"Similarity score (0-100, mean overlap vs the other page): {mean_ov}")
    if names_a:
        print(f"  {100.0 * len(both) / len(names_a):.1f}% of A's cards appear on B's page")
    if names_b:
        print(f"  {100.0 * len(both) / len(names_b):.1f}% of B's cards appear on A's page")
    dice = dice_similarity_pct(names_a, names_b)
    print(f"Dice similarity (0-100): {dice}")
    if names_a or names_b:
        union = names_a | names_b
        jacc = 100.0 * len(both) / len(union) if union else 0.0
        print(f"Jaccard (intersection / union): {jacc:.1f}%")

    print()
    if min_inclusion_both_pct is not None:
        print(f"Cards in both with inclusion > {min_inclusion_both_pct:g}% on each page:")
    else:
        print("Cards in both (EDHREC deck inclusion % on each page):")
    print(f"{'Card':<50}  {'A %':>10}  {'B %':>10}")
    print("-" * 76)
    for name in sorted(table_names, key=str.lower):
        _pa, ia = cards_a.get(name, (None, "—"))
        _pb, ib = cards_b.get(name, (None, "—"))
        print(f"{name[:50]:<50}  {ia:>10}  {ib:>10}")


__all__ = [
    "dice_similarity_pct",
    "mean_overlap_pct",
    "fetch_commander_cards_inclusions",
    "inclusion_label",
    "inclusion_pct",
    "parse_edhrec_commander_slug",
    "run_compare_edhrec_commanders",
]
