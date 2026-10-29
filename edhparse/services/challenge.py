"""'32 deck challenge' data: one commander per color identity, grouped for an infographic.

Turns a list of EDHREC commander URLs/names into editable entries:
  * the Scryfall card(s) behind each commander (double-faced cards keep both faces,
    partner/background pairs keep both cards),
  * its color identity (the union for pairs),
  * EDHREC's top archetypes (taglinks) to pick from,
  * a default art crop, plus a way to list every other artwork for the art picker.
"""

from __future__ import annotations

import hashlib
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

import requests

from clients import scryfall_bulk
from clients.edhrec_client import EdhrecClient
from clients.edhrec_prefetch import prefetch_urls
from clients.session import create_cached_session, ensure_scryfall_bulk
from core.deck import Deck
from edhparse.parsing import load_commander_slugs_from_file
from renderers.summary_renderer import _GUILD_NAMES
from utils.http_retry import get_with_retry

PAIR_SEP = " // "
EDHREC_COMMANDER_URL = "https://json.edhrec.com/pages/commanders/{slug}.json"
SCRYFALL_SEARCH_URL = "https://api.scryfall.com/cards/search"
MAX_THEMES = 5
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

# Rows of the infographic: (label, number of colors in each identity of the row)
ROW_LABELS = {
    0: "Colorless & Mono",
    1: "Two-Color",
    2: "Three-Color",
    3: "Four & Five-Color",
}


def identity_groups() -> List[dict]:
    """All 32 color identities in display order, with a display name and infographic row."""
    groups = []
    for key, name in _GUILD_NAMES.items():
        n = len(key)
        row = 0 if n <= 1 else 1 if n == 2 else 2 if n == 3 else 3
        groups.append({"key": key, "name": name, "row": row, "colors": list(key)})
    return groups


def canonical_identity(colors: Iterable[str]) -> str:
    present = set(colors or [])
    return "".join(c for c in "WUBRG" if c in present)


# ---------------------------------------------------------------- card data


def _faces_of(card: dict) -> List[dict]:
    """Face list for a Scryfall card: 2+ faces only when each face has its own picture."""
    if card.get("card_faces") and not card.get("image_uris"):
        faces = []
        for face in card["card_faces"]:
            faces.append(
                {
                    "name": face.get("name"),
                    "art": (face.get("image_uris") or {}).get("art_crop"),
                    "img": (face.get("image_uris") or {}).get("large") or (face.get("image_uris") or {}).get("normal"),
                    "artist": face.get("artist") or card.get("artist"),
                }
            )
        return faces
    return [
        {
            "name": card.get("name"),
            "art": (card.get("image_uris") or {}).get("art_crop"),
            "img": (card.get("image_uris") or {}).get("large") or (card.get("image_uris") or {}).get("normal"),
            "artist": card.get("artist"),
        }
    ]


def _card_entry(card: dict) -> dict:
    return {
        "name": card.get("name"),
        "oracle_id": card.get("oracle_id"),
        "layout": card.get("layout"),
        "print_id": card.get("id"),
        "set": card.get("set"),
        "type_line": card.get("type_line"),
        "faces": _faces_of(card),
    }


def cards_for_header(header: str) -> List[dict]:
    """Scryfall cards for an EDHREC commander header.

    EDHREC lists double-faced commanders by their front face and partner/background pairs as
    "A // B". A real card (even a double-faced one) resolves directly; otherwise "A // B" is
    treated as two separate cards.
    """
    header = (header or "").strip()
    if not header:
        return []
    card = scryfall_bulk.get_card_by_name_or_face(header)
    if card is not None:
        return [card]
    if PAIR_SEP in header:
        cards = [scryfall_bulk.get_card_by_name_or_face(part.strip()) for part in header.split(PAIR_SEP)]
        if cards and all(c is not None for c in cards):
            return cards
    return []


def top_themes(page: dict, limit: int = MAX_THEMES) -> List[dict]:
    tags = (page.get("panels") or {}).get("taglinks") or []

    def _count(tag):
        try:
            return int(tag.get("count") or 0)
        except (TypeError, ValueError):
            return 0

    out = []
    for tag in sorted(tags, key=_count, reverse=True):
        slug = tag.get("slug") or ""
        label = tag.get("value") or tag.get("label") or slug.replace("-", " ").title()
        if label:
            out.append({"slug": slug, "label": label, "count": _count(tag)})
        if len(out) >= limit:
            break
    return out


def build_entry(slug: str, client: EdhrecClient) -> Tuple[Optional[dict], Optional[str]]:
    """One infographic entry for an EDHREC commander slug, or (None, reason)."""
    page, err = client.get_json(EDHREC_COMMANDER_URL.format(slug=slug))
    if err or not page:
        return None, f"{slug}: EDHREC page unavailable ({err or 'empty'})"
    header = Deck._normalize_commander_header(page.get("header") or "")
    cards = cards_for_header(header)
    if not cards:
        return None, f"{slug}: could not find '{header}' in Scryfall data"

    colors = set()
    for card in cards:
        colors.update(card.get("color_identity") or [])
    themes = top_themes(page)
    return (
        {
            "id": slug,
            "slug": slug,
            "title": header,
            "identity": canonical_identity(colors),
            "cards": [_card_entry(c) for c in cards],
            "themes": themes,
            "theme": themes[0]["label"] if themes else "",
            "face_mode": "all",
            "edhrec_url": f"https://edhrec.com/commanders/{slug}",
        },
        None,
    )


def slugs_from_text(text: str) -> List[str]:
    """EDHREC URLs / names (one per line) -> unique commander slugs, in order."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as tmp:
        tmp.write(text or "")
        tmp_path = Path(tmp.name)
    try:
        pairs = load_commander_slugs_from_file(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    return _clean_slugs(slug for slug, _theme in pairs)


def _clean_slugs(slugs: Iterable[str]) -> List[str]:
    """Drop stray quotes/punctuation (e.g. a trailing apostrophe) and de-duplicate, keeping order."""
    cleaned = (re.sub(r"[^a-z0-9-]+", "", (s or "").lower()).strip("-") for s in slugs)
    return list(dict.fromkeys(s for s in cleaned if s))


def slugs_from_file(path: str | Path) -> List[str]:
    return _clean_slugs(slug for slug, _theme in load_commander_slugs_from_file(path))


def build_entries(slugs: Iterable[str], *, session=None, fresh_cache: bool = False, workers: int = 4) -> Dict[str, Any]:
    """Resolve many commanders. Returns {"entries": [...], "errors": [...]}."""
    slugs = _clean_slugs(slugs)
    session = session or create_cached_session(fresh_cache=fresh_cache)
    if not scryfall_bulk.is_available():
        ensure_scryfall_bulk(session)
    prefetch_urls(
        session,
        [EDHREC_COMMANDER_URL.format(slug=s) for s in slugs],
        workers=workers,
        label="commander pages",
    )
    client = EdhrecClient(session)
    entries, errors = [], []
    for slug in slugs:
        entry, problem = build_entry(slug, client)
        if entry:
            entries.append(entry)
        else:
            errors.append(problem)
    return {"entries": entries, "errors": errors}


# ---------------------------------------------------------------- art picker


def _style_label(card: dict) -> str:
    """Short frame description for the picker (Borderless, Showcase, Extended art, ...)."""
    parts = []
    if card.get("border_color") == "borderless":
        parts.append("Borderless")
    effects = card.get("frame_effects") or []
    for key, label in (("showcase", "Showcase"), ("extendedart", "Extended art"), ("etched", "Etched"), ("inverted", "Inverted")):
        if key in effects:
            parts.append(label)
    if card.get("full_art"):
        parts.append("Full art")
    if card.get("promo"):
        parts.append("Promo")
    if card.get("finishes") and set(card["finishes"]) == {"foil"}:
        parts.append("Foil")
    return " · ".join(parts)


def fetch_prints(oracle_id: str, *, session=None, max_pages: int = 8) -> List[dict]:
    """Every distinct artwork for a card (newest first), with per-face art crops."""
    if not UUID_RE.match(oracle_id or ""):
        raise ValueError("invalid oracle id")
    session = session or create_cached_session()
    params: Optional[dict] = {
        "q": f"oracleid:{oracle_id}",
        "unique": "prints",
        "order": "released",
        "dir": "desc",
        "include_extras": "true",
        "include_variations": "true",
    }
    url = SCRYFALL_SEARCH_URL
    prints: List[dict] = []
    for _ in range(max_pages):
        response, err = get_with_retry(
            session,
            url,
            params=params,
            timeout=15,
            headers={"User-Agent": scryfall_bulk.SCRYFALL_USER_AGENT, "Accept": "application/json;q=0.9,*/*;q=0.8"},
        )
        if err or response is None:
            if prints:
                break
            raise RuntimeError(f"Scryfall search failed: {err}")
        if not getattr(response, "from_cache", True):
            time.sleep(0.12)
        payload = response.json()
        for card in payload.get("data", []):
            faces = _faces_of(card)
            if not any(f.get("art") for f in faces):
                continue
            prints.append(
                {
                    "id": card.get("id"),
                    "set": card.get("set"),
                    "set_name": card.get("set_name"),
                    "released_at": card.get("released_at"),
                    "collector_number": card.get("collector_number"),
                    "artist": faces[0].get("artist") or card.get("artist"),
                    "style": _style_label(card),
                    "faces": faces,
                }
            )
        if not payload.get("has_more") or not payload.get("next_page"):
            break
        url, params = payload["next_page"], None
    return prints


# ---------------------------------------------------------------- image proxy

IMAGE_HOST_SUFFIXES = (".scryfall.io", ".scryfall.com")
IMAGE_CACHE_DIR = Path("cache/img")
_EXT_BY_TYPE = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/svg+xml": ".svg"}
_TYPE_BY_EXT = {v: k for k, v in _EXT_BY_TYPE.items()}


def is_allowed_image_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(host.endswith(suffix) for suffix in IMAGE_HOST_SUFFIXES)


def fetch_image(url: str) -> Tuple[bytes, str]:
    """Image bytes + content type for a Scryfall image URL, cached on disk.

    The infographic is exported from the browser; serving images from our own origin keeps
    the canvas untainted and lets exports work offline once an image has been seen.
    """
    if not is_allowed_image_url(url):
        raise ValueError("image host not allowed")
    key = hashlib.sha1(url.encode("utf-8")).hexdigest()
    IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for path in IMAGE_CACHE_DIR.glob(key + ".*"):
        return path.read_bytes(), _TYPE_BY_EXT.get(path.suffix, "application/octet-stream")
    response = requests.get(
        url,
        headers={"User-Agent": scryfall_bulk.SCRYFALL_USER_AGENT, "Accept": "image/*"},
        timeout=20,
        allow_redirects=False,
    )
    response.raise_for_status()
    content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if content_type not in _EXT_BY_TYPE:
        raise ValueError(f"unexpected content type {content_type!r}")
    (IMAGE_CACHE_DIR / (key + _EXT_BY_TYPE[content_type])).write_bytes(response.content)
    return response.content, content_type


# ---------------------------------------------------------------- saved layouts


def safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", name or "").strip("-.")
    return cleaned[:80] or "challenge"
