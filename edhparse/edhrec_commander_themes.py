"""
Discover EDHREC commander theme (sub-archetype) paths and build json.edhrec.com URLs.

Price tiers combine as: .../commander[/theme][/budget|expensive].json
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.deck import EDHREC_SLEEP, _edhrec_get_with_retry

from .edhrec_commander_compare import inclusion_stat_display

# EDHREC hub nav links to deck "tracks" / power tiers — not card-style archetypes.
_DECK_TRACK_SLUGS = frozenset({"exhibition", "core", "upgraded", "optimized", "cedh"})


def edhrec_commander_json_url(commander_slug: str, *, theme_slug: str = "", tier_slug: str = "") -> str:
    """Build https://json.edhrec.com/pages/commanders/...json for tier/theme."""
    base = (commander_slug or "").strip().strip("/")
    parts = ["https://json.edhrec.com/pages/commanders"] + base.split("/")
    ts = (theme_slug or "").strip().strip("/")
    if ts:
        parts.append(ts)
    tier = (tier_slug or "").strip().strip("/")
    if tier:
        parts.append(tier)
    return "/".join(parts) + ".json"


def discover_theme_slugs(
    session: Any,
    commander_slug: str,
    *,
    max_themes: int = 5,
    timeout: int = 15,
) -> List[str]:
    """
    Return up to ``max_themes`` theme path segments (e.g. ``treasure``) found on the
    EDHREC commander hub HTML, excluding budget/expensive and deck-track nav slugs.
    Each slug is verified with a lightweight JSON request before inclusion.
    """
    slug = (commander_slug or "").strip().strip("/")
    if not slug:
        return []

    hub_url = "https://edhrec.com/commanders/" + slug
    response, err = _edhrec_get_with_retry(session, hub_url, timeout=timeout)
    if err or response is None:
        return []
    if not getattr(response, "from_cache", True):
        time.sleep(EDHREC_SLEEP)
    try:
        html = response.text
    except Exception:
        return []

    prefix = "/commanders/" + slug.lower() + "/"
    pat = re.compile(r'href=["\']' + re.escape(prefix) + r"([a-z0-9-]+)", re.I)
    ordered: List[str] = []
    seen: set[str] = set()
    for m in pat.finditer(html):
        seg = m.group(1).lower()
        if seg in ("budget", "expensive") or seg in _DECK_TRACK_SLUGS:
            continue
        if seg in seen:
            continue
        seen.add(seg)
        ordered.append(seg)

    verified: List[str] = []
    for theme in ordered:
        if len(verified) >= max_themes:
            break
        url = edhrec_commander_json_url(slug, theme_slug=theme, tier_slug="")
        r2, err2 = _edhrec_get_with_retry(session, url, timeout=timeout)
        if err2 or r2 is None:
            continue
        if not getattr(r2, "from_cache", True):
            time.sleep(EDHREC_SLEEP)
        try:
            r2.json()
        except Exception:
            continue
        verified.append(theme)
    return verified


def extract_page_cards_and_meta(
    data: dict,
    *,
    fmt_synergy: Callable[..., Optional[str]],
) -> Tuple[List[str], Dict[str, Dict[str, Optional[str]]]]:
    """
    From one commander JSON payload, return card names in first-seen order (all sections)
    and per-name EDHREC stats from the first cardview for that name on this page.
    """
    container = data.get("container") or {}
    json_dict = container.get("json_dict") or {}
    cardlists = json_dict.get("cardlists") or []

    ordered_names: List[str] = []
    seen_name: set[str] = set()
    meta: Dict[str, Dict[str, Optional[str]]] = {}

    for section in cardlists:
        for cv in section.get("cardviews") or []:
            name = (cv.get("name") or "").strip()
            if not name:
                continue
            if name not in meta:
                meta[name] = {
                    "inclusion": inclusion_stat_display(cv),
                    "synergy": fmt_synergy(cv.get("synergy")),
                }
            if name not in seen_name:
                seen_name.add(name)
                ordered_names.append(name)

    return ordered_names, meta


def theme_label_from_container(container: dict, theme_slug: str) -> str:
    """Human label for a themed commander JSON ``container``."""
    if not (theme_slug or "").strip():
        return "Average"
    title = (container.get("title") or "").strip()
    if " - " in title:
        tail = title.rsplit(" - ", 1)[-1]
        tail = tail.replace("(Commander)", "").strip()
        if tail:
            return tail
    return (theme_slug or "").replace("-", " ").title()
