"""Zulu's Games (Shopify) product search with polite rate limiting."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Set
from urllib.parse import quote_plus

import requests

from utils.http_retry import get_with_retry

ZULUS_BASE = "https://zulusgames.com"
ZULUS_SEARCH = f"{ZULUS_BASE}/search"
# Polite gap between *live* requests. Cache hits skip this.
DEFAULT_SLEEP_SECONDS = 0.75
# Exactly one HTTP attempt per card — no 429 retry storms (those multiply load).
MAX_HTTP_ATTEMPTS = 1

# Conditions the shopper wants: NM/LP, foil or non-foil. Exclude MP/HP/DMG.
ALLOWED_CONDITIONS: Set[str] = {
    "near mint",
    "lightly played",
    "near mint foil",
    "lightly played foil",
}

_HTML_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
_PRODUCT_CARD_SPLIT = re.compile(r"(?=<[^>]+productCard__card)")
_PRODUCT_TITLE = re.compile(
    r'productCard__title[^>]*>\s*<a href="(?P<href>[^"]+)"[^>]*>\s*(?P<title>[^<]+?)\s*</a>',
    re.I,
)
_PRODUCT_SET = re.compile(r'productCard__setName[^>]*>\s*(?P<set>[^<]+?)\s*<', re.I)
_PRODUCT_CHIP = re.compile(r"<li\b[^>]*\bproductChip\b[^>]*>", re.I)
_CHIP_ATTR = re.compile(
    r'data-variant(?P<key>Available|Id|Price|Title)="(?P<value>[^"]*)"',
    re.I,
)

StatusFn = Callable[[str], None]


@dataclass
class ZulusOffer:
    title: str
    price_usd: float
    url: str
    available: bool = True
    handle: str = ""
    condition: str = ""
    variant_id: Optional[int] = None


@dataclass
class FetchMeta:
    """Diagnostics for one HTTP attempt."""

    source: str = "live"  # cache | live | fail
    elapsed_ms: int = 0
    bytes: int = 0
    status: Optional[int] = None
    rate_limited: bool = False
    challenged: bool = False
    throttled_ms: int = 0


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).casefold()


def _title_matches_card(product_title: str, card_name: str) -> bool:
    """
    Accept Shopify titles like 'Sol Ring [Fallout]' or DFCs with //,
    reject clearly different cards that merely contain the query.
    """
    title = (product_title or "").strip()
    card = (card_name or "").strip()
    if not title or not card:
        return False

    base = re.sub(r"\s*\[[^\]]*\]\s*$", "", title).strip()
    card_l = _normalize(card)
    base_l = _normalize(base)

    def _exact_or_treated(candidate: str, want: str) -> bool:
        if candidate == want:
            return True
        if candidate.startswith(want + " ("):
            return True
        return False

    if _exact_or_treated(base_l, card_l):
        return True

    if " // " in card_l:
        front = card_l.split(" // ", 1)[0].strip()
        if _exact_or_treated(base_l, front):
            return True
        if " // " in base_l and base_l.split(" // ", 1)[0].strip() == front:
            return True
    if " // " in base_l:
        front = base_l.split(" // ", 1)[0].strip()
        if front == card_l:
            return True
    return False


def _condition_allowed(variant_title: str) -> bool:
    return _normalize(variant_title) in ALLOWED_CONDITIONS


def _wildcard_query(card_name: str) -> str:
    """Match Zulus storefront search: *card name*."""
    name = (card_name or "").strip()
    if not name:
        return ""
    if name.startswith("*") and name.endswith("*"):
        return name
    return f"*{name}*"


def _handle_from_url(path_or_url: str) -> str:
    text = (path_or_url or "").strip()
    if not text:
        return ""
    path = text.split("?", 1)[0]
    marker = "/products/"
    if marker in path:
        return path.split(marker, 1)[1].strip("/")
    return path.strip("/")


def _absolute_url(path_or_url: str, variant_id: Optional[int] = None) -> str:
    text = (path_or_url or "").strip()
    if not text:
        return ZULUS_BASE
    if text.startswith("http://") or text.startswith("https://"):
        base = text.split("?", 1)[0]
    else:
        if not text.startswith("/"):
            text = "/" + text
        base = ZULUS_BASE + text.split("?", 1)[0]
    if variant_id:
        return f"{base}?variant={variant_id}"
    return base


def _parse_chip_attrs(tag: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for match in _CHIP_ATTR.finditer(tag):
        out[match.group("key").casefold()] = match.group("value")
    return out


def is_challenge_html(html: Optional[str]) -> bool:
    """Cloudflare / bot interstitial — not a real search results page."""
    if not html:
        return True
    low = html.casefold()
    if "verifying your connection" in low:
        return True
    if "cf-mitigated" in low or "just a moment" in low:
        return True
    # Full search pages are huge; tiny HTML without product cards is suspicious.
    if "productcard__card" not in low and len(html) < 40000 and "results found" not in low:
        return True
    return False


def parse_search_html(html: str, card_name: str) -> List[ZulusOffer]:
    """
    Parse Zulus search-results HTML for in-stock NM/LP offers.

    Each product card exposes condition chips with availability, price (cents),
    and variant id — the same data shown in the storefront grid.
    """
    if not html or "productCard__card" not in html:
        return []

    offers: List[ZulusOffer] = []
    for part in _PRODUCT_CARD_SPLIT.split(html)[1:]:
        title_m = _PRODUCT_TITLE.search(part)
        if not title_m:
            continue
        href = title_m.group("href")
        card_title = title_m.group("title").strip()
        set_m = _PRODUCT_SET.search(part)
        set_name = set_m.group("set").strip() if set_m else ""
        full_title = f"{card_title} [{set_name}]" if set_name else card_title
        if not _title_matches_card(full_title, card_name):
            continue

        handle = _handle_from_url(href)
        product_url = href.split("?", 1)[0]
        for chip_tag in _PRODUCT_CHIP.findall(part):
            attrs = _parse_chip_attrs(chip_tag)
            if attrs.get("available", "").casefold() != "true":
                continue
            condition = (attrs.get("title") or "").strip()
            if not _condition_allowed(condition):
                continue
            raw_price = attrs.get("price")
            try:
                price = int(raw_price) / 100.0
            except (TypeError, ValueError):
                continue
            vid_raw = attrs.get("id")
            try:
                vid_int = int(vid_raw) if vid_raw else None
            except (TypeError, ValueError):
                vid_int = None
            offers.append(
                ZulusOffer(
                    title=full_title,
                    price_usd=price,
                    url=_absolute_url(product_url, vid_int),
                    available=True,
                    handle=handle,
                    condition=condition,
                    variant_id=vid_int,
                )
            )
    return offers


class ZulusClient:
    def __init__(
        self,
        session: Optional[requests.Session] = None,
        *,
        sleep_seconds: float = DEFAULT_SLEEP_SECONDS,
        on_status: Optional[StatusFn] = None,
    ):
        self.session = session or requests.Session()
        # Real browser UA — Cloudflare challenges the previous bot-style string.
        self.session.headers["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        )
        self.session.headers.setdefault("Accept-Language", "en-US,en;q=0.9")
        self.base_sleep = max(0.0, float(sleep_seconds))
        self.sleep_seconds = self.base_sleep
        self.on_status = on_status
        self._last_call = 0.0
        self.stats = {
            "requests": 0,
            "cache_hits": 0,
            "live": 0,
            "rate_limits": 0,
            "challenges": 0,
            "live_ms": 0,
        }

    def _status(self, message: str) -> None:
        print(message, flush=True)
        if self.on_status is not None:
            try:
                self.on_status(message)
            except Exception:
                pass

    def _throttle(self) -> int:
        if self.sleep_seconds <= 0:
            return 0
        elapsed = time.time() - self._last_call
        wait = self.sleep_seconds - elapsed
        if wait <= 0:
            return 0
        time.sleep(wait)
        return int(wait * 1000)

    def _note_rate_limit(self) -> None:
        # After a 429, slow subsequent live calls to avoid long retry storms.
        self.sleep_seconds = max(self.sleep_seconds, min(2.0, self.base_sleep * 3 or 1.0))
        self.stats["rate_limits"] += 1

    def _note_success(self) -> None:
        # Gradually recover toward the configured base delay.
        if self.sleep_seconds > self.base_sleep:
            self.sleep_seconds = max(self.base_sleep, self.sleep_seconds * 0.85)

    def _is_cached(self, url: str, params: Optional[dict] = None) -> bool:
        cache = getattr(self.session, "cache", None)
        if cache is None:
            return False
        try:
            # requests-cache 1.2 contains() does not take params=; build a real request.
            # Include the same Accept we send so Vary: Accept matches.
            req = self.session.prepare_request(
                requests.Request("GET", url, params=params, headers={"Accept": _HTML_ACCEPT})
            )
            return bool(cache.contains(request=req))
        except Exception:
            return False

    def _drop_cached(self, response) -> None:
        """Remove a bad cached body (e.g. challenge page that slipped in)."""
        cache = getattr(self.session, "cache", None)
        if cache is None or response is None:
            return
        try:
            key = getattr(response, "cache_key", None)
            if key:
                cache.delete(key)
                return
        except Exception:
            pass
        try:
            url = getattr(response, "url", None)
            if url:
                cache.delete(urls=[url])
        except Exception:
            pass

    def _get_text(
        self, url: str, *, params: Optional[dict] = None, timeout: int = 30
    ) -> tuple[Optional[str], FetchMeta]:
        meta = FetchMeta()
        hit_429 = {"value": False}

        def _on_rate_limited(attempt: int, max_attempts: int, wait: int) -> None:
            hit_429["value"] = True
            self._status(
                f"Zulus 429 on '{url}' — not retrying "
                f"(single-attempt mode; next cards use longer delay)"
            )

        cached = self._is_cached(url, params)
        throttled_ms = 0
        if not cached:
            throttled_ms = self._throttle()
        meta.throttled_ms = throttled_ms

        started = time.perf_counter()
        response, err = get_with_retry(
            self.session,
            url,
            params=params,
            timeout=timeout,
            max_retries=MAX_HTTP_ATTEMPTS,
            on_rate_limited=_on_rate_limited,
            # Stable Accept is required: Zulus sends Vary: Accept, so a different
            # Accept (e.g. session-level application/json from Scryfall) misses cache.
            headers={"Accept": _HTML_ACCEPT},
        )
        meta.elapsed_ms = int((time.perf_counter() - started) * 1000)
        self.stats["requests"] += 1

        from_cache = bool(getattr(response, "from_cache", False)) if response is not None else cached
        if hit_429["value"]:
            meta.rate_limited = True
            self._note_rate_limit()
        if not from_cache:
            self._last_call = time.time()
            if response is not None and not hit_429["value"]:
                self._note_success()
            self.stats["live"] += 1
            self.stats["live_ms"] += meta.elapsed_ms
            meta.source = "live"
        else:
            self.stats["cache_hits"] += 1
            meta.source = "cache"

        if err or response is None:
            meta.source = "fail"
            return None, meta

        meta.status = getattr(response, "status_code", None)
        text = response.text
        meta.bytes = len(text or "")
        if is_challenge_html(text):
            meta.challenged = True
            self.stats["challenges"] += 1
            # Never keep interstitials around for the next run.
            self._drop_cached(response)
        return text, meta

    def search_card(self, card_name: str) -> List[ZulusOffer]:
        """
        Exactly one storefront search request per card.

        That HTML grid already has in-stock NM/LP prices per printing — no
        pagination, no unfiltered fallback, no per-product .js fetches.
        """
        query = _wildcard_query(card_name)
        if not query:
            return []

        t0 = time.perf_counter()
        # In-stock filter keeps the single page focused on buyable copies.
        params = {
            "page": "1",
            "q": query,
            "filter.v.availability": "1",
        }
        html, meta = self._get_text(ZULUS_SEARCH, params=params)

        if html is None:
            self._status(
                f"Zulus fail '{card_name}': {meta.source} {meta.elapsed_ms}ms"
                + (f" (throttled {meta.throttled_ms}ms)" if meta.throttled_ms else "")
            )
            return []

        if meta.challenged:
            self._status(
                f"Zulus challenge/block '{card_name}' "
                f"({meta.source} {meta.elapsed_ms}ms, {meta.bytes}B) — skipping card"
            )
            return []

        if "productCard__card" not in html:
            self._status(
                f"Zulus empty '{card_name}' "
                f"({meta.source} {meta.elapsed_ms}ms, {meta.bytes}B)"
            )
            return []

        offers = parse_search_html(html, card_name)
        offers.sort(key=lambda o: (o.price_usd, o.title.casefold(), o.condition.casefold()))
        cards = html.count("productCard__card")
        total_ms = int((time.perf_counter() - t0) * 1000)

        detail = (
            f"Zulus '{card_name}': {meta.source} {meta.elapsed_ms}ms "
            f"{meta.bytes // 1024}KB, {cards} cards, {len(offers)} NM/LP"
        )
        if meta.throttled_ms:
            detail += f", wait {meta.throttled_ms}ms"
        if meta.rate_limited:
            detail += ", 429"
        self._status(detail)

        if offers:
            best = offers[0]
            self._status(
                f"Zulus best '{card_name}': ${best.price_usd:.2f} {best.condition} "
                f"({len(offers)} offers, {total_ms}ms total, delay now {self.sleep_seconds:.2f}s)"
            )
        else:
            self._status(
                f"Zulus none '{card_name}' ({total_ms}ms total, "
                f"delay now {self.sleep_seconds:.2f}s)"
            )
        return offers

    def cheapest(self, card_name: str) -> Optional[ZulusOffer]:
        offers = self.search_card(card_name)
        return offers[0] if offers else None


def search_url_for(card_name: str) -> str:
    return f"{ZULUS_BASE}/search?page=1&q={quote_plus(_wildcard_query(card_name))}"
