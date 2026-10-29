"""Polite EDHREC access for the evaluator: cache first, hard request budget, jittered delays.

Verified behaviour of the live endpoints (October 2026):

* ``json.edhrec.com`` is a static S3/CloudFront bucket. It ignores query strings and returns
  **403 AccessDenied (XML)** — not 404 — for pages that do not exist.
* Real "with card / without card" filtering only exists on the website
  (``edhrec.com/commanders/<slug>?f=in=a;in=b;out=c``). The numbers are embedded in the
  Next.js flight payload (``self.__next_f.push``) of the HTML, which we parse.
* Card hub pages (``/pages/cards/<slug>.json``) give ``lift`` directly and have no ``synergy``.
* Partner pages use alphabetically sorted slugs; the reverse order returns ``{"redirect": ...}``.
"""

from __future__ import annotations

import json
import random
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import quote, urlparse

from edhparse.evaluate import config as C
from edhparse.evaluate.models import CardStat, Cohort

# --------------------------------------------------------------------------- slugs


def edhrec_slug(name: str) -> str:
    """EDHREC ``sanitized`` slug for a card/commander *as EDHREC names it*.

    Matches 13,374 of 13,375 real slugs seen in the EDHREC cache: accents folded, apostrophes
    and punctuation dropped, runs of anything else become ``-``. Split cards / Rooms keep both
    halves ("Wear // Tear" -> "wear-tear"); pass a front-face name for MDFCs.
    """
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"['’.,!?\"]", "", text)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def commander_slug(names: Sequence[str]) -> str:
    """Single commander slug, or partner/background pair slugs sorted alphabetically."""
    slugs = [edhrec_slug(n.split(" // ", 1)[0]) for n in names if n]
    if len(slugs) <= 1:
        return slugs[0] if slugs else ""
    return "-".join(sorted(slugs[:2]))


# --------------------------------------------------------------------------- URLs


def commander_url(slug: str, *segments: str) -> str:
    path = "/".join([slug, *[s for s in segments if s]])
    return f"{C.EDHREC_JSON_BASE}/commanders/{path}.json"


def average_deck_url(slug: str, variant: str = "") -> str:
    path = f"{slug}/{variant}" if variant else slug
    return f"{C.EDHREC_JSON_BASE}/average-decks/{path}.json"


def card_hub_url(card_slug: str) -> str:
    return f"{C.EDHREC_JSON_BASE}/cards/{card_slug}.json"


def filter_query(include: Iterable[str], exclude: Iterable[str] = ()) -> str:
    parts = [f"in={s}" for s in sorted(include)] + [f"out={s}" for s in sorted(exclude)]  # order-invariant URL
    return ";".join(parts)


def filtered_page_url(slug: str, include: Iterable[str], exclude: Iterable[str] = (), segments: Sequence[str] = ()) -> str:
    path = "/".join([slug, *[s for s in segments if s]])
    q = filter_query(include, exclude)
    url = f"{C.EDHREC_SITE_BASE}/commanders/{path}"
    return f"{url}?f={quote(q, safe='')}" if q else url


# --------------------------------------------------------------------------- parsing


def _float(v: Any) -> Optional[float]:
    if v is None or v == "$undefined":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _stat_from_view(view: dict, page_n: int, category: str = "") -> Optional[CardStat]:
    name = view.get("name")
    if not name:
        return None
    pot = _float(view.get("potential_decks", view.get("potentialDecks")))
    pot_i = int(pot) if pot and pot > 0 else int(page_n or 0)
    n = _float(view.get("num_decks", view.get("numDecks")))
    if n is None:
        inc = _float(view.get("inclusion"))
        if inc is None:
            return None
        n = inc * pot_i if inc <= 1.0 else (inc / 100.0 * pot_i if pot_i > 100 and inc <= 100 else inc)
    n = min(max(n, 0.0), float(pot_i or n))
    synergy = _float(view.get("synergy"))
    if synergy is not None and abs(synergy) > 1.0:
        synergy /= 100.0
    return CardStat(
        name=name,
        slug=view.get("sanitized") or view.get("slug") or view.get("cardId") or edhrec_slug(name),
        num_decks=int(round(n)),
        potential_decks=pot_i,
        synergy=synergy,
        lift=_float(view.get("lift")),
        category=category or view.get("category") or "",
    )


def parse_json_page(data: dict, label: str, url: str = "", source: str = "json") -> Cohort:
    """EDHREC JSON page (commander, theme, price tier, average deck or card hub) -> Cohort."""
    container = (data or {}).get("container") or {}
    jd = container.get("json_dict") or {}
    card = jd.get("card") or {}
    n_total = card.get("num_decks") or data.get("num_decks") or jd.get("num_decks") or 0
    cohort = Cohort(label=label, url=url, num_decks=int(n_total or 0), source=source, raw=data)
    lists = jd.get("cardlists") or data.get("cardlists") or []
    for cl in lists:
        tag = cl.get("tag") or ""
        if "commanders" in tag:  # hub pages also list commanders that run the card
            continue
        for view in cl.get("cardviews") or []:
            stat = _stat_from_view(view, cohort.num_decks, tag)
            if stat:
                cohort.add(stat)
    if not cohort.num_decks and cohort.cards:
        cohort.num_decks = max(s.potential_decks for s in cohort.cards.values())
    return cohort


_FLIGHT_RE = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)')


def extract_flight(html: str) -> str:
    chunks = []
    for m in _FLIGHT_RE.finditer(html or ""):
        try:
            chunks.append(json.loads('"' + m.group(1) + '"'))
        except ValueError:
            continue
    return "".join(chunks)


def parse_filtered_html(html: str, label: str, url: str = "", commander: str = "") -> Cohort:
    """Parse the card recommendations embedded in an edhrec.com commander page."""
    flight = extract_flight(html)
    decoder = json.JSONDecoder()
    recs: List[dict] = []
    seen_starts = set()
    # Every card recommendation is a flat object carrying "numDecks"; anchor on that key
    # (not on key order) and decode from the enclosing "{".
    for m in re.finditer(r'"numDecks":', flight):
        start = flight.rfind("{", 0, m.start())
        if start < 0 or start in seen_starts:
            continue
        seen_starts.add(start)
        try:
            obj, _end = decoder.raw_decode(flight, start)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("name"):
            recs.append(obj)
    n_total = None
    for r in recs:
        if r.get("role") == "commander" and isinstance(r.get("numDecks"), (int, float)):
            if not commander or r.get("cardId") == commander or n_total is None:
                n_total = int(r["numDecks"])
    cohort = Cohort(label=label, url=url, num_decks=int(n_total or 0), source="html-filter")
    for r in recs:
        if r.get("role") == "commander" or not isinstance(r.get("numDecks"), (int, float)):
            continue
        stat = _stat_from_view(r, cohort.num_decks, r.get("category") or "")
        if stat:
            cohort.add(stat)
    if n_total is None and cohort.cards:
        cohort.num_decks = max(s.potential_decks for s in cohort.cards.values())
    return cohort


def merge_cohorts(label: str, cohorts: Sequence[Cohort]) -> Cohort:
    """Merge commander pages (e.g. two partners) weighting each by log sample size."""
    import math

    usable = [c for c in cohorts if c and c.num_decks > 0]
    merged = Cohort(label=label, num_decks=sum(c.num_decks for c in usable), source="merged")
    if not usable:
        return merged
    weights = [math.log(max(c.num_decks, 2)) for c in usable]
    wsum = sum(weights)
    names = {}
    for c in usable:
        for n in c.names():
            names.setdefault(n.lower(), n)
    for n in names.values():
        # p_est, not p_raw: a card unlisted on one partner's page is under that page's floor, not at 0%.
        p = sum(w * c.p_est(n) for w, c in zip(weights, usable)) / wsum
        lift = sum(w * c.lift(n) for w, c in zip(weights, usable)) / wsum
        slug = next((c.get(n).slug for c in usable if c.get(n)), edhrec_slug(n))
        merged.add(
            CardStat(name=n, slug=slug, num_decks=int(round(p * merged.num_decks)),
                     potential_decks=merged.num_decks, lift=lift)
        )
    merged.raw = _merge_raw([c.raw for c in usable], weights)
    return merged


def _merge_raw(raws: Sequence[Optional[dict]], weights: Optional[Sequence[float]] = None) -> Optional[dict]:
    """Combine the structural panels of several commander pages.

    Type counts and the mana curve are per-deck averages, so they are averaged with the same
    weights as the card rates (an unlisted bucket counts as 0). Theme-link counts are deck counts
    and add up. One partner's card lists are dropped rather than left posing as the merged page.
    """
    weights = list(weights) if weights is not None else [1.0] * len(raws)
    items = [(r, w) for r, w in zip(raws, weights) if r]
    if not items:
        return None
    wsum = sum(w for _, w in items) or 1.0
    merged = dict(items[0][0])
    merged.pop("container", None)
    for t in C.CARD_TYPES:
        key = t.lower()
        if any(r.get(key) is not None for r, _ in items):
            merged[key] = sum(w * (r.get(key) or 0) for r, w in items) / wsum
    panels = dict(merged.get("panels") or {})
    curve_keys = {k for r, _ in items for k in ((r.get("panels") or {}).get("mana_curve") or {})}
    panels["mana_curve"] = {k: sum(w * (((r.get("panels") or {}).get("mana_curve") or {}).get(k) or 0)
                                   for r, w in items) / wsum for k in curve_keys}
    links: Dict[str, dict] = {}
    for r, _ in items:
        for t in (r.get("panels") or {}).get("taglinks") or []:
            slug = t.get("slug")
            if not slug:
                continue
            if slug in links:
                links[slug]["count"] = (links[slug].get("count") or 0) + (t.get("count") or 0)
            else:
                links[slug] = dict(t)
    panels["taglinks"] = list(links.values())
    merged["panels"] = panels
    return merged


# --------------------------------------------------------------------------- client


@dataclass
class Telemetry:
    edhrec_requests: int = 0
    edhrec_cache_hits: int = 0
    scryfall_requests: int = 0
    scryfall_cache_hits: int = 0
    missing: List[str] = field(default_factory=list)
    blocked_hosts: Set[str] = field(default_factory=set)
    budget_exhausted: bool = False
    static_cdn_ignores_query_params: bool = False
    filter_parse_failed: bool = False
    filter_mode: str = ""
    log: List[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        """True once any host has challenged us (per-host state lives in ``blocked_hosts``)."""
        return bool(self.blocked_hosts)

    def note(self, msg: str) -> None:
        self.log.append(msg)


class RequestBudget:
    def __init__(self, max_requests: int):
        self.max_requests = int(max_requests)
        self.used = 0

    @property
    def remaining(self) -> int:
        return max(self.max_requests - self.used, 0)

    def can_spend(self, n: int = 1) -> bool:
        return self.used + n <= self.max_requests

    def spend(self) -> None:
        self.used += 1


class JitterRateLimiter:
    """Leaky-bucket style spacing: at least uniform(lo, hi) seconds between network calls."""

    def __init__(self, delay_range: Tuple[float, float], sleep: Callable[[float], None] = time.sleep,
                 rand: Callable[[float, float], float] = random.uniform, clock: Callable[[], float] = time.monotonic):
        self.lo, self.hi = delay_range
        self._sleep, self._rand, self._clock = sleep, rand, clock
        self._last: Optional[float] = None

    def wait(self) -> None:
        if self._last is not None:
            gap = self._rand(self.lo, self.hi)
            elapsed = self._clock() - self._last
            if elapsed < gap:
                self._sleep(gap - elapsed)
        self._last = self._clock()


def _is_s3_missing(resp) -> bool:
    ctype = (resp.headers.get("Content-Type") or resp.headers.get("content-type") or "").lower()
    try:
        head = (resp.text or "")[:400]
    except Exception:
        head = ""
    return "xml" in ctype or "<Code>AccessDenied</Code>" in head or "NoSuchKey" in head


def _is_challenge(resp) -> bool:
    if (resp.headers.get("cf-mitigated") or "").lower() == "challenge":
        return True
    try:
        head = (resp.text or "")[:4000].casefold()
    except Exception:
        return False
    return ("just a moment" in head and "cloudflare" in head) or "verifying your connection" in head


_SOFT_429_RE = re.compile(r"<title>[^<]{0,40}429 Too Many Requests", re.I)


def is_soft_rate_limit(text: Optional[str]) -> bool:
    """edhrec.com answers a burst with HTTP *200* and a '429 Too Many Requests' page."""
    return bool(text) and bool(_SOFT_429_RE.search(text[:60000]))


def _cache_age_seconds(resp) -> Optional[float]:
    created = getattr(resp, "created_at", None)
    if not isinstance(created, datetime):
        return None
    now = datetime.now(timezone.utc) if created.tzinfo else datetime.utcnow()
    return (now - created).total_seconds()


class EdhrecFetcher:
    """Cache-first GETs with a hard request budget and a circuit breaker.

    ``session`` is a requests-cache ``CachedSession`` (``clients.session.create_cached_session``)
    or anything with the same ``get(url, only_if_cached=..., force_refresh=..., ...)`` signature.
    """

    def __init__(self, session, *, max_requests: int = C.DEFAULT_MAX_REQUESTS,
                 ttl_days: float = C.DEFAULT_CACHE_TTL_DAYS, telemetry: Optional[Telemetry] = None,
                 sleep: Callable[[float], None] = time.sleep, rand: Callable[[float, float], float] = random.uniform,
                 missing_cache_path: Optional[str] = "cache/edhrec_missing.json"):
        self.session = session
        self.budget = RequestBudget(max_requests)
        self.ttl_seconds = float(ttl_days) * 86400.0
        self.telemetry = telemetry or Telemetry()
        self.limiter = JitterRateLimiter(C.EDHREC_DELAY_RANGE, sleep=sleep, rand=rand)
        self.web_limiter = JitterRateLimiter(C.EDHREC_WEB_DELAY_RANGE, sleep=sleep, rand=rand)
        self._sleep = sleep
        self._missing: Dict[str, bool] = {}
        self._memo: Dict[str, Any] = {}
        self._missing_client = None
        if missing_cache_path:
            try:
                from clients.edhrec_client import EdhrecClient

                self._missing_client = EdhrecClient(session, missing_cache_path=missing_cache_path)
            except Exception:  # pragma: no cover - optional persistence
                self._missing_client = None

    # -- cache ----------------------------------------------------------------
    def _cached(self, url: str):
        try:
            resp = self.session.get(url, only_if_cached=True, timeout=10)
        except Exception:
            return None
        if getattr(resp, "status_code", 0) != 200 or not getattr(resp, "from_cache", False):
            return None
        if is_soft_rate_limit(getattr(resp, "text", "")):
            return None  # a rate-limit page that was cached earlier: ignore it and re-fetch
        return resp

    def is_cached(self, url: str) -> bool:
        resp = self._cached(url)
        if resp is None:
            return False
        age = _cache_age_seconds(resp)
        return age is None or age <= self.ttl_seconds

    def _remember_missing(self, url: str) -> None:
        self._missing[url] = True
        self.telemetry.missing.append(url)
        if self._missing_client is not None:
            try:
                self._missing_client._record_missing(url)  # shared with the rest of edh-parse
            except Exception:
                pass

    def _known_missing(self, url: str) -> bool:
        if url in self._missing:
            return True
        if self._missing_client is not None:
            try:
                return self._missing_client.is_known_missing(url)
            except Exception:
                return False
        return False

    # -- fetch ----------------------------------------------------------------
    @staticmethod
    def _host(url: str) -> str:
        return urlparse(url).netloc.lower()

    def _limiter_for(self, url: str) -> JitterRateLimiter:
        return self.web_limiter if self._host(url) == "edhrec.com" else self.limiter

    def is_blocked(self, url: str) -> bool:
        """The circuit breaker is per host: a challenge on edhrec.com leaves json.edhrec.com usable."""
        return self._host(url) in self.telemetry.blocked_hosts

    def has_slack(self, keep: int = 0) -> bool:
        """Can one more network request be spent while leaving ``keep`` slots untouched?"""
        return self.budget.can_spend(1 + keep)

    def fetch(self, url: str, kind: str = "json", _depth: int = 0, keep: int = 0):
        """Return parsed JSON (dict) / HTML (str), or None when missing / blocked / out of budget.

        ``keep`` reserves that many request slots for later callers (S3 hubs): a fetch that would
        dip into them is skipped without declaring the whole budget exhausted.
        """
        if url in self._memo:
            return self._memo[url]
        if self._known_missing(url):
            return None
        cached = self._cached(url)
        if cached is not None:
            age = _cache_age_seconds(cached)
            fresh = age is None or age <= self.ttl_seconds
            if fresh or self.is_blocked(url) or not self.budget.can_spend():
                self.telemetry.edhrec_cache_hits += 1
                return self._finish(url, cached, kind, _depth, keep)
        if self.is_blocked(url):
            self.telemetry.note(f"skipped (circuit breaker open): {url}")
            return None
        if not self.budget.can_spend():
            self.telemetry.budget_exhausted = True
            self.telemetry.note(f"skipped (request budget of {self.budget.max_requests} used): {url}")
            return None
        if not self.has_slack(keep):
            self.telemetry.note(f"skipped (last {keep} requests reserved for hub pages): {url}")
            return None
        resp = self._network_get(url)
        if resp is None:
            if cached is not None:  # refresh failed (rate limit, challenge, error): an old copy beats no data
                self.telemetry.edhrec_cache_hits += 1
                self.telemetry.note(f"refresh failed; serving stale cache: {url}")
                return self._finish(url, cached, kind, _depth, keep)
            return None
        return self._finish(url, resp, kind, _depth, keep)

    def _network_get(self, url: str):
        attempt = 0
        while True:
            if not self.budget.can_spend():
                self.telemetry.budget_exhausted = True
                return None
            self._limiter_for(url).wait()
            self.budget.spend()
            self.telemetry.edhrec_requests += 1
            try:
                resp = self.session.get(
                    url,
                    timeout=20,
                    force_refresh=True,
                    expire_after=self.ttl_seconds,
                    headers={"User-Agent": C.USER_AGENT, "Accept": "application/json, text/html"},
                )
            except Exception as exc:
                self.telemetry.note(f"network error {url}: {exc}")
                return None
            status = getattr(resp, "status_code", 0)
            if status == 200 and is_soft_rate_limit(getattr(resp, "text", "")):
                status = 429
            if status in (429, 503) and attempt < len(C.BACKOFF_SCHEDULE):
                retry_after = resp.headers.get("Retry-After")
                try:
                    wait = float(retry_after) if retry_after else C.BACKOFF_SCHEDULE[attempt]
                except ValueError:
                    wait = C.BACKOFF_SCHEDULE[attempt]
                self.telemetry.note(f"HTTP {status} from EDHREC; backing off {wait:.0f}s")
                self._sleep(wait)
                attempt += 1
                continue
            if status == 429:  # still limited after every backoff: stop asking this host
                self.telemetry.blocked_hosts.add(self._host(url))
                self.telemetry.note(f"EDHREC rate-limited {self._host(url)} (HTTP 429) after {attempt} backoff retries; "
                                    "using cached data only for that host")
                return None
            if status == 404 or (status == 403 and _is_s3_missing(resp)):
                self._remember_missing(url)
                return None
            if status == 403 or (status == 200 and _is_challenge(resp)):
                self.telemetry.blocked_hosts.add(self._host(url))
                self.telemetry.note(f"EDHREC blocked the request (HTTP {status}) on {self._host(url)}; "
                                    "using cached data only for that host")
                return None
            if status != 200:
                self.telemetry.note(f"HTTP {status} for {url}")
                return None
            return resp

    def _finish(self, url: str, resp, kind: str, depth: int, keep: int = 0):
        if kind == "html":
            payload: Any = resp.text
        else:
            try:
                payload = resp.json()
            except ValueError:
                self.telemetry.note(f"not JSON: {url}")
                return None
            if isinstance(payload, dict) and payload.get("redirect") and depth < 2:
                target = str(payload["redirect"]).strip("/")
                new_url = f"{C.EDHREC_JSON_BASE}/{target}.json"
                self.telemetry.note(f"redirect {url} -> {new_url}")
                payload = self.fetch(new_url, kind, depth + 1, keep)
        self._memo[url] = payload
        return payload
