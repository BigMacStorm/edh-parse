"""Card metadata (local Scryfall bulk DB) and functional-role tags (Scryfall oracle tags).

Metadata comes from ``clients.scryfall_bulk`` so enriching a deck plus several hundred
candidates costs zero HTTP requests. Roles come from Scryfall's community oracle tags
(``otag:``), queried only for cards whose tags are not already in the local SQLite cache.
"""

from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence

from edhparse.evaluate import config as C
from edhparse.evaluate.edhrec import Telemetry
from edhparse.evaluate.models import CardMeta, front_face, name_key

_FRONT_FACE_LAYOUTS = {"modal_dfc", "transform", "adventure", "flip", "meld", "double_faced_token", "prepare"}
_SYMBOL_RE = re.compile(r"\{([^}]+)\}")


def mana_value(cost: str) -> float:
    total = 0.0
    for sym in _SYMBOL_RE.findall(cost or ""):
        s = sym.upper()
        if s.isdigit():
            total += int(s)
        elif s in ("X", "Y", "Z"):
            continue
        elif s == "HALF" or s.startswith("H"):
            total += 0.5
        else:
            total += 1  # coloured, hybrid, phyrexian, snow, colourless
    return total


def _price(card: dict) -> tuple:
    prices = card.get("prices") or {}
    for key in ("usd", "usd_foil", "usd_etched"):
        try:
            val = prices.get(key)
            if val not in (None, ""):
                return float(val), True
        except (TypeError, ValueError):
            continue
    return C.DEFAULT_PRICE, False


def meta_from_scryfall(card: dict) -> CardMeta:
    """Normalise a Scryfall card object (handles MDFC, transform, adventure, split, Rooms)."""
    name = card.get("name") or ""
    layout = card.get("layout") or "normal"
    faces = card.get("card_faces") or []
    front_type = card.get("type_line") or ""
    if faces and layout in _FRONT_FACE_LAYOUTS:
        front_type = faces[0].get("type_line") or front_type.split(" // ", 1)[0]
    oracle = card.get("oracle_text") or "\n".join(f.get("oracle_text") or "" for f in faces)
    cmc = float(card.get("cmc") or 0.0)
    if layout == "split" and faces:
        # Split cards and Rooms report the *sum* of both halves; the cheaper half is what
        # you can cast first, so use that for curve purposes.
        values = [mana_value(f.get("mana_cost") or "") for f in faces if f.get("mana_cost")]
        if values:
            cmc = min(values)
    is_land = "Land" in front_type.split("—", 1)[0]
    is_mdfc_land = bool(
        layout == "modal_dfc" and not is_land and len(faces) > 1 and "Land" in (faces[1].get("type_line") or "")
    )
    price, known = _price(card)
    legal = (card.get("legalities") or {}).get("commander", "legal") == "legal"
    return CardMeta(
        name=name,
        front=front_face(name),
        cmc=cmc,
        color_identity=tuple(card.get("color_identity") or ()),
        type_line=front_type,
        oracle_text=oracle,
        price=price,
        price_known=known,
        layout=layout,
        is_land=is_land,
        is_mdfc_land=is_mdfc_land,
        legal=legal,
    )


def manapool_price_lookup(session=None, max_age_days: float = 7.0) -> Optional[Callable[[str], Optional[float]]]:
    """Cheapest NM price per card from edh-parse's ManaPool feed cache (downloads if missing/stale)."""
    try:
        from clients.manapool import load_price_index

        index = load_price_index(session, max_age_seconds=int(max_age_days * 86400))
    except Exception as exc:  # network down, feed changed...
        print(f"ManaPool prices unavailable ({exc}); using Scryfall prices only.")
        return None

    def lookup(name: str) -> Optional[float]:
        offer = index.lookup(name)
        return offer.price_usd if offer else None

    return lookup


def edhrec_display_name(meta: CardMeta) -> str:
    """EDHREC lists DFC-style cards by their front face and split cards by the full name."""
    if meta.layout in _FRONT_FACE_LAYOUTS:
        return meta.front
    return meta.name


class CardMetaLookup:
    """Name -> CardMeta via the local Scryfall bulk DB (memoised), with live Scryfall API fallback."""

    def __init__(self, lookup: Optional[Callable[[str], Optional[dict]]] = None,
                 price_lookup: Optional[Callable[[str], Optional[float]]] = None,
                 session=None,
                 enable_api_fallback: bool = True):
        self._custom_lookup = lookup is not None
        if lookup is None:
            from clients import scryfall_bulk

            lookup = scryfall_bulk.get_card_by_name_or_face
        self._lookup = lookup
        # Optional cheaper/better price source (ManaPool cheapest NM). Scryfall's oracle_cards
        # bulk picks one printing per card; for ~1,200 legal cards that printing is MTGO-only
        # and has no USD price at all.
        self._price_lookup = price_lookup
        self._session = session
        self._enable_api_fallback = enable_api_fallback and not self._custom_lookup
        self._memo: Dict[str, CardMeta] = {}

    def get(self, name: str) -> CardMeta:
        key = name_key(name)
        if key in self._memo:
            return self._memo[key]
        card = None
        try:
            card = self._lookup(name)
            if card is None and " // " in name:
                card = self._lookup(front_face(name))
        except Exception:
            card = None
        if card is None and self._enable_api_fallback:
            # Fallback for Universes Beyond / Secret Lair flavor names (e.g. "Lightning, Lone Commando" -> Isshin)
            # or new printings not in the bulk dump.
            try:
                from clients.scryfall_client import ScryfallClient
                from clients.session import create_cached_session
                sess = self._session or create_cached_session()
                client = ScryfallClient(sess)
                card, _ = client.get_card_by_name_exact(name)
                if not card and " // " in name:
                    card, _ = client.get_card_by_name_exact(front_face(name))
            except Exception:
                card = None
        if card:
            meta = meta_from_scryfall(card)
            if self._price_lookup is not None and not meta.is_basic:
                try:
                    alt = self._price_lookup(meta.name) or self._price_lookup(meta.front)
                except Exception:
                    alt = None
                if alt is not None and alt > 0:
                    meta.price, meta.price_known = float(alt), True
            if meta.is_basic:
                meta.price, meta.price_known = 0.0, True
        else:
            meta = CardMeta(name=name, front=front_face(name), found=False)
        self._memo[key] = meta
        if meta.found:
            self._memo[name_key(meta.name)] = meta
            self._memo[name_key(meta.front)] = meta
        return meta


# --------------------------------------------------------------------------- roles

# Used only if Scryfall cannot be reached; noticeably less accurate than the oracle tags.
_FALLBACK_ROLE_RE = {
    "RAMP": re.compile(
        r"(add \{|add (one|two|three) mana|mana of any (color|type)|search your library for (a|up to \w+) basic land"
        r"|search your library for (a|up to \w+) .*land cards?.*onto the battlefield|create (a|two|three) treasure)",
        re.I,
    ),
    "DRAW": re.compile(r"(draw (two|three|four|x|\d+) cards|whenever .* draw a card|draw a card for each|at the beginning of .* draw)", re.I),
    "REMOVAL": re.compile(
        r"(destroy target|exile target (artifact|creature|enchantment|planeswalker|permanent|nonland)|target .* gets -[\dx]+/-[\dx]+"
        r"|counter target|deals \d+ damage to (any target|target creature)|target player sacrifices)",
        re.I,
    ),
    "BOARD_WIPE": re.compile(
        r"(destroy all|exile all (creatures|artifacts|enchantments|nonland)|all creatures get -|each creature gets -"
        r"|damage to each creature|each player sacrifices all|return all .* to their owners'? hands"
        r"|return each nonland permanent)",
        re.I,
    ),
}


def fallback_roles(meta: CardMeta) -> set:
    roles = set()
    for role, rx in _FALLBACK_ROLE_RE.items():
        if rx.search(meta.oracle_text or ""):
            roles.add(role)
    return _apply_role_rules(meta, roles)


def _apply_role_rules(meta: CardMeta, roles: Iterable[str]) -> set:
    out = set(roles)
    if "RAMP" in out and (meta.is_land or meta.cmc > C.RAMP_MAX_CMC):
        out.discard("RAMP")
    if meta.is_land:
        out.discard("DRAW")
    return out


def chunk_names(names: Sequence[str], max_chars: int = C.SCRYFALL_QUERY_NAME_CHARS) -> List[List[str]]:
    chunks: List[List[str]] = []
    cur: List[str] = []
    length = 0
    for n in names:
        token = '"' + n.replace('"', "") + '"'
        if cur and length + len(token) + 4 > max_chars:
            chunks.append(cur)
            cur, length = [], 0
        cur.append(n)
        length += len(token) + 4
    if cur:
        chunks.append(cur)
    return chunks


def build_role_query(role: str, names: Sequence[str]) -> str:
    clause = " or ".join('"' + n.replace('"', "") + '"' for n in names)
    return f"{C.ROLE_TAG_QUERIES[role]} ({clause})"


class RoleTagCache:
    def __init__(self, path: str = "cache/evaluate_role_tags.sqlite"):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS role_tags (name TEXT, role TEXT, has INTEGER, fetched REAL,"
            " PRIMARY KEY (name, role))"
        )

    def get(self, name: str, role: str, ttl_seconds: float) -> Optional[bool]:
        row = self.conn.execute(
            "SELECT has, fetched FROM role_tags WHERE name = ? AND role = ?", (name_key(name), role)
        ).fetchone()
        if not row or (time.time() - row[1]) > ttl_seconds:
            return None
        return bool(row[0])

    def put_many(self, rows: Iterable[tuple]) -> None:
        now = time.time()
        self.conn.executemany(
            "INSERT OR REPLACE INTO role_tags (name, role, has, fetched) VALUES (?, ?, ?, ?)",
            [(name_key(n), r, int(bool(h)), now) for n, r, h in rows],
        )
        self.conn.commit()


class RoleTagger:
    """Assigns RAMP / DRAW / REMOVAL / BOARD_WIPE from Scryfall oracle tags."""

    def __init__(self, session, telemetry: Telemetry, cache: Optional[RoleTagCache] = None,
                 sleep: Callable[[float], None] = time.sleep, ttl_days: float = C.ROLE_TAG_TTL_DAYS):
        self.session = session
        self.telemetry = telemetry
        self._cache = cache
        self._sleep = sleep
        self.ttl = ttl_days * 86400.0
        self._last = 0.0
        self.failed = False

    @property
    def cache(self) -> RoleTagCache:
        if self._cache is None:  # created on first use so constructing an Evaluator has no side effects
            self._cache = RoleTagCache()
        return self._cache

    def _search(self, query: str) -> Optional[List[dict]]:
        url = C.SCRYFALL_SEARCH_URL
        params = {"q": query, "unique": "cards"}
        cards: List[dict] = []
        while url:
            attempt = 0
            while True:
                gap = time.monotonic() - self._last
                if gap < C.SCRYFALL_MIN_INTERVAL:
                    self._sleep(C.SCRYFALL_MIN_INTERVAL - gap)
                self._last = time.monotonic()
                try:
                    resp = self.session.get(
                        url, params=params, timeout=20, expire_after=self.ttl,
                        headers={"User-Agent": C.USER_AGENT, "Accept": "application/json"},
                    )
                except Exception as exc:
                    self.telemetry.note(f"Scryfall tag query failed: {exc}")
                    return None
                status = resp.status_code
                if status in (429, 503) and attempt < len(C.BACKOFF_SCHEDULE):
                    retry_after = getattr(resp, "headers", {}).get("Retry-After")
                    try:
                        wait = float(retry_after) if retry_after else C.BACKOFF_SCHEDULE[attempt]
                    except (ValueError, TypeError):
                        wait = C.BACKOFF_SCHEDULE[attempt]
                    self.telemetry.note(f"HTTP {status} from Scryfall; backing off {wait:.0f}s (attempt {attempt + 1})")
                    self._sleep(wait)
                    attempt += 1
                    continue
                break

            if getattr(resp, "from_cache", False):
                self.telemetry.scryfall_cache_hits += 1
            else:
                self.telemetry.scryfall_requests += 1
            if resp.status_code == 404:
                return cards  # no card matched
            if resp.status_code != 200:
                self.telemetry.note(f"Scryfall tag query HTTP {resp.status_code}")
                return None
            data = resp.json()
            cards.extend(data.get("data") or [])
            url = data.get("next_page") if data.get("has_more") else None
            params = None
        return cards

    def tag(self, metas: Iterable[CardMeta]) -> None:
        metas = [m for m in metas if m.found]
        by_front: Dict[str, List[CardMeta]] = {}
        for m in metas:
            by_front.setdefault(name_key(m.front), []).append(m)
        found: Dict[str, set] = {k: set() for k in by_front}
        to_store = []
        for role in C.ROLES:
            pending = []
            for key, ms in by_front.items():
                cached = self.cache.get(key, role, self.ttl)
                if cached is None:
                    pending.append(ms[0].front)
                elif cached:
                    found[key].add(role)
            if self.failed:
                break
            for chunk in chunk_names(pending):
                result = self._search(build_role_query(role, chunk))
                if result is None:
                    self.failed = True
                    break
                hits = {name_key(front_face(c.get("name") or "")) for c in result}
                hits |= {name_key(c.get("name") or "") for c in result}
                for n in chunk:
                    has = name_key(n) in hits
                    if has:
                        found[name_key(n)].add(role)
                    to_store.append((n, role, has))
        if to_store:
            self.cache.put_many(to_store)
        if self.failed:
            self.telemetry.note("Scryfall oracle tags unavailable; fell back to oracle-text regexes for roles.")
            for m in metas:
                m.roles = fallback_roles(m)
            return
        for key, ms in by_front.items():
            for m in ms:
                m.roles = _apply_role_rules(m, found[key])
