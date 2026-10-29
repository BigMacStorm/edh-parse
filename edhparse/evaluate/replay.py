"""Record and replay EDHREC pages (plus frozen card metadata) for offline real-data backtests.

Dev tooling, not part of a normal evaluation run:

* ``RecordingSession`` wraps a real session and funnels every EDHREC response through a
  ``FixtureWriter``, which trims it to the fields the parsers read.
* ``ReplaySession`` serves those recordings back with the same ``get`` signature, so the real
  ``EdhrecFetcher`` / ``Evaluator`` code paths (redirects, S3 403s, circuit breaker) run unchanged.
* ``SnapshotMetaLookup`` / ``SnapshotTagger`` freeze Scryfall metadata, prices and role tags so a
  backtest does not drift when the local Scryfall or price caches are refreshed.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qs, quote, urlparse

from edhparse.evaluate.edhrec import _cache_age_seconds, extract_flight, is_soft_rate_limit
from edhparse.evaluate.models import CardMeta, front_face, name_key
from edhparse.evaluate.scryfall_meta import _apply_role_rules

MANIFEST = "manifest.json"
_CARDVIEW_KEYS = ("name", "sanitized", "slug", "cardId", "num_decks", "numDecks", "potential_decks",
                  "potentialDecks", "inclusion", "synergy", "lift", "category")
_TYPE_KEYS = ("creature", "instant", "sorcery", "artifact", "enchantment", "planeswalker", "land")
_REC_KEYS = ("name", "numDecks", "potentialDecks", "synergy", "lift", "role", "cardId", "category", "inclusion",
             "sanitized")


# --------------------------------------------------------------------------- trimming


def trim_edhrec_json(data: Any) -> Any:
    """Keep only what ``parse_json_page`` and the engine read from an EDHREC JSON page."""
    if not isinstance(data, dict):
        return data
    if "redirect" in data:
        return {"redirect": data["redirect"]}
    out: Dict[str, Any] = {k: data[k] for k in ("num_decks", *_TYPE_KEYS) if k in data}
    panels = data.get("panels") or {}
    if panels:
        trimmed = {}
        if "mana_curve" in panels:
            trimmed["mana_curve"] = panels["mana_curve"]
        if "taglinks" in panels:
            trimmed["taglinks"] = [{k: t[k] for k in ("slug", "count", "value") if k in t}
                                   for t in panels["taglinks"] or []]
        out["panels"] = trimmed
    deck = data.get("deck")
    if isinstance(deck, dict) and "cards" in deck:
        out["deck"] = {"cards": deck["cards"]}
    jd = (data.get("container") or {}).get("json_dict")
    if isinstance(jd, dict):
        card = jd.get("card") or {}
        trimmed_jd: Dict[str, Any] = {"card": {k: card[k] for k in ("name", "sanitized", "num_decks") if k in card}}
        if "num_decks" in jd:
            trimmed_jd["num_decks"] = jd["num_decks"]
        trimmed_jd["cardlists"] = [
            {"tag": cl.get("tag"),
             "cardviews": [{k: v[k] for k in _CARDVIEW_KEYS if k in v} for v in cl.get("cardviews") or []]}
            for cl in jd.get("cardlists") or []
        ]
        out["container"] = {"json_dict": trimmed_jd}
    return out


def trim_filtered_html(html: str) -> str:
    """Reduce an edhrec.com page to a minimal Next.js flight payload holding just the card records."""
    flight = extract_flight(html)
    decoder = json.JSONDecoder()
    recs: List[dict] = []
    seen = set()
    for m in re.finditer(r'"numDecks":', flight):
        start = flight.rfind("{", 0, m.start())
        if start < 0 or start in seen:
            continue
        seen.add(start)
        try:
            obj, _ = decoder.raw_decode(flight, start)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("name"):
            recs.append({k: obj[k] for k in _REC_KEYS if k in obj})
    payload = "0:" + json.dumps({"recs": recs}, separators=(",", ":"))
    return "<html><script>self.__next_f.push([1," + json.dumps(payload) + "])</script></html>"


# --------------------------------------------------------------------------- recording


def canonical_url(url: str) -> str:
    """Order-invariant form of a filter URL: ``?f=in=b;in=a`` equals ``?f=in=a;in=b``."""
    parts = urlparse(url)
    f = parse_qs(parts.query).get("f")
    if not f:
        return url
    tokens = sorted(t for t in f[0].split(";") if t)
    return f"{parts.scheme}://{parts.netloc}{parts.path}?f={quote(';'.join(tokens), safe='')}"


def _is_edhrec(url: str) -> bool:
    return urlparse(url).netloc.lower().endswith("edhrec.com")


class FixtureWriter:
    """Collects responses (last one per URL wins) and writes ``manifest.json`` + trimmed page files."""

    def __init__(self, directory, meta: Optional[dict] = None, compress: bool = True):
        self.dir = Path(directory)
        self.meta = meta or {}
        self.compress = compress
        self._entries: Dict[str, Dict[str, Any]] = {}

    def add(self, url: str, status: int, content_type: str, text: str) -> None:
        self._entries[canonical_url(url)] = {"url": url, "status": int(status), "content_type": content_type,
                                             "text": text}

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        routes: Dict[str, dict] = {}
        for key, e in sorted(self._entries.items()):
            route = {"status": e["status"], "content_type": e["content_type"]}
            is_html = "html" in (e["content_type"] or "").lower()
            if e["status"] == 200:
                body = trim_filtered_html(e["text"]) if is_html else json.dumps(
                    trim_edhrec_json(json.loads(e["text"])), separators=(",", ":"))
                ext = "html" if is_html else "json"
            else:
                body, ext = e["text"], "xml"
            name = f"p_{hashlib.sha1(key.encode()).hexdigest()[:10]}.{ext}"
            if self.compress:
                name += ".gz"
                (self.dir / name).write_bytes(gzip.compress(body.encode("utf-8"), mtime=0))  # mtime=0: stable bytes
            else:
                (self.dir / name).write_text(body, encoding="utf-8")
            route["file"] = name
            routes[key] = route
        manifest = {"recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **self.meta,
                    "routes": routes}
        (self.dir / MANIFEST).write_text(json.dumps(manifest, indent=1), encoding="utf-8")


class RecordingSession:
    """Delegates to a real session and records every EDHREC response the fetcher actually uses."""

    def __init__(self, inner, writer: FixtureWriter, max_age_seconds: Optional[float] = None):
        self.inner = inner
        self.writer = writer
        self.max_age_seconds = max_age_seconds  # cache hits older than this get refreshed by the fetcher, so skip them

    def get(self, url, *args, only_if_cached=False, **kw):
        resp = self.inner.get(url, *args, only_if_cached=only_if_cached, **kw)
        if _is_edhrec(url) and not is_soft_rate_limit(getattr(resp, "text", "")):
            status = getattr(resp, "status_code", 0)
            served_from_cache = only_if_cached and status == 200 and getattr(resp, "from_cache", False)
            if served_from_cache and self.max_age_seconds is not None:
                age = _cache_age_seconds(resp)
                served_from_cache = age is None or age <= self.max_age_seconds
            if served_from_cache or (not only_if_cached and status in (200, 403, 404)):
                ctype = (getattr(resp, "headers", {}) or {}).get("Content-Type", "")
                self.writer.add(url, status, ctype, resp.text)
        return resp


# --------------------------------------------------------------------------- replay


class ReplayResponse:
    def __init__(self, status: int, text: str, content_type: str):
        self.status_code = status
        self.text = text
        self.headers = {"Content-Type": content_type}
        self.from_cache = False
        self.created_at = datetime.now(timezone.utc)

    def json(self):
        return json.loads(self.text)


class ReplaySession:
    """Offline stand-in for a requests session, backed by a directory written by ``FixtureWriter``."""

    def __init__(self, directory):
        self.dir = Path(directory)
        manifest = json.loads((self.dir / MANIFEST).read_text(encoding="utf-8"))
        self.routes: Dict[str, dict] = manifest["routes"]
        self.requested: List[str] = []
        self.unrecorded: List[str] = []

    def get(self, url, params=None, only_if_cached=False, **kw):
        if only_if_cached:
            return ReplayResponse(504, "", "text/plain")  # nothing cached: take the (replayed) network path
        self.requested.append(url)
        route = self.routes.get(canonical_url(url))
        if route is None:
            self.unrecorded.append(url)
            return ReplayResponse(504, "", "text/plain")
        return ReplayResponse(route["status"], _read_page(self.dir / route["file"]), route["content_type"])


def _read_page(path: Path) -> str:
    if path.suffix == ".gz":
        return gzip.decompress(path.read_bytes()).decode("utf-8")
    return path.read_text(encoding="utf-8")


def compress_fixture_dir(directory) -> int:
    """Gzip every plain page file of a fixture directory in place; returns how many were converted."""
    directory = Path(directory)
    manifest_path = directory / MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    converted = 0
    for route in manifest["routes"].values():
        if route["file"].endswith(".gz"):
            continue
        src = directory / route["file"]
        route["file"] += ".gz"
        (directory / route["file"]).write_bytes(gzip.compress(src.read_bytes(), mtime=0))
        src.unlink()
        converted += 1
    manifest_path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return converted


# --------------------------------------------------------------------------- frozen card metadata


def snapshot_from_metas(metas: Iterable[CardMeta], tagged: bool = False) -> Dict[str, dict]:
    """Serialisable card table keyed by lowercase full name (front faces resolve at lookup).

    ``tagged`` records that every card's roles were looked up, not just those the evaluator tagged
    on the recording run; ``SnapshotTagger`` reports cards without it.
    """
    out: Dict[str, dict] = {}
    for m in metas:
        if not m.found:
            continue
        d = asdict(m)
        d["roles"] = sorted(m.roles)
        d["color_identity"] = list(m.color_identity)
        d["tagged"] = bool(tagged)
        out[name_key(m.name)] = d
    return out


class SnapshotMetaLookup:
    """Drop-in for ``CardMetaLookup`` that answers from a frozen snapshot (zero I/O)."""

    def __init__(self, snapshot: Dict[str, dict]):
        self._snap = snapshot
        self._by_front = {name_key(d["front"]): k for k, d in snapshot.items()}
        self._memo: Dict[str, CardMeta] = {}

    def get(self, name: str) -> CardMeta:
        key = name_key(name)
        if key in self._memo:
            return self._memo[key]
        snap_key = key if key in self._snap else self._by_front.get(name_key(front_face(name)))
        if snap_key is None:
            meta = CardMeta(name=name, front=front_face(name), found=False)
        else:
            d = dict(self._snap[snap_key])
            d.pop("tagged", None)
            d["roles"] = set()  # roles are applied by the tagger, as in a live run
            d["color_identity"] = tuple(d["color_identity"])
            meta = CardMeta(**d)
        self._memo[key] = meta
        return meta


class SnapshotTagger:
    """Applies the role tags frozen in a snapshot (stands in for ``RoleTagger``)."""

    failed = False

    def __init__(self, snapshot: Dict[str, dict]):
        self._roles = {k: set(d.get("roles") or ()) for k, d in snapshot.items()}
        self._tagged = {k: bool(d.get("tagged")) for k, d in snapshot.items()}
        self._by_front = {name_key(d["front"]): k for k, d in snapshot.items()}
        self.untagged: List[str] = []  # cards asked for whose roles were never actually looked up

    def tag(self, metas: Iterable[CardMeta]) -> None:
        for m in metas:
            key = name_key(m.name) if name_key(m.name) in self._roles else self._by_front.get(name_key(m.front))
            # re-apply the live rules so threshold changes (e.g. RAMP_MAX_CMC) take effect on replay
            m.roles = _apply_role_rules(m, self._roles.get(key, ()))
            if not self._tagged.get(key, False) and m.name not in self.untagged:
                self.untagged.append(m.name)
