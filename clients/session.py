"""Shared HTTP session factory for Scryfall / EDHREC caches."""

from __future__ import annotations

import random
import re
import sqlite3
from pathlib import Path
from typing import Optional

import requests_cache

DEFAULT_CACHE_PATH = "cache/card_cache"
DEFAULT_EXPIRE_SECONDS = 3600 * 24 * 31

# The Scryfall bulk-data listing is how we notice that new bulk data (and prices) exist,
# so it must not be served from a 31-day-old cache entry.
BULK_LIST_EXPIRE_SECONDS = 3600 * 24
# Cache entries get a TTL of base * (1 +/- jitter) so a big cold run doesn't make
# everything expire on the same day and force another full cold run.
TTL_JITTER = 0.2
# Anything bigger than this is not an API/JSON response (e.g. an old bulk download that
# was cached by mistake) and is removed from the cache database.
MAX_CACHED_BODY_BYTES = 8_000_000


def _should_cache_response(response) -> bool:
    """
    Persist successful bodies, but never keep Cloudflare/bot interstitials.
    Those often return HTTP 200 and would otherwise poison the cache for 31 days.
    """
    try:
        status = int(getattr(response, "status_code", 0) or 0)
    except (TypeError, ValueError):
        return False
    if status != 200:
        return False
    # Only sniff the prefix — enough for challenge pages, cheap on huge HTML.
    try:
        head = (response.text or "")[:4000].casefold()
    except Exception:
        return True
    if "verifying your connection" in head:
        return False
    if "just a moment" in head and "cloudflare" in head:
        return False
    if "cf-mitigated" in head:
        return False
    # edhrec.com rate limits with an HTTP 200 "429 Too Many Requests" page; caching it would
    # serve that page for a month.
    try:
        if re.search(r"<title>[^<]{0,40}429 too many requests", (response.text or "")[:60000], re.I):
            return False
    except Exception:
        pass
    return True


def _ttl_for(url: str, base_expire: int) -> float:
    if "api.scryfall.com/bulk-data" in str(url):
        return BULK_LIST_EXPIRE_SECONDS
    return base_expire * random.uniform(1 - TTL_JITTER, 1 + TTL_JITTER)


class _JitteredCachedSession(requests_cache.CachedSession):
    """CachedSession with per-URL expiry rules (short for the bulk listing, jittered otherwise)."""

    def __init__(self, *args, base_expire: int = DEFAULT_EXPIRE_SECONDS, **kwargs):
        super().__init__(*args, expire_after=base_expire, **kwargs)
        self._base_expire = base_expire

    def request(self, method, url, *args, **kwargs):
        if kwargs.get("expire_after") is None:
            kwargs["expire_after"] = _ttl_for(url, self._base_expire)
        return super().request(method, url, *args, **kwargs)


def prune_oversized_cache_entries(cache_path: str = DEFAULT_CACHE_PATH, max_bytes: int = MAX_CACHED_BODY_BYTES) -> int:
    """Delete oversized cached responses (older versions cached the 25-100MB Scryfall bulk files).

    Returns the number of rows removed. Cheap when there is nothing to remove.
    """
    db_file = Path(f"{cache_path}.sqlite")
    if not db_file.is_file():
        return 0
    try:
        conn = sqlite3.connect(str(db_file), timeout=30)
        try:
            cur = conn.execute("DELETE FROM responses WHERE length(value) > ?", (max_bytes,))
            removed = cur.rowcount or 0
            conn.commit()
            if removed:
                conn.execute("VACUUM")
        finally:
            conn.close()
        return removed
    except sqlite3.Error:
        return 0


def create_cached_session(
    cache_path: str = DEFAULT_CACHE_PATH,
    *,
    expire_after: int = DEFAULT_EXPIRE_SECONDS,
    fresh_cache: bool = False,
) -> requests_cache.CachedSession:
    """Create a CachedSession, optionally clearing the on-disk cache first."""
    Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
    removed = prune_oversized_cache_entries(cache_path)
    if removed:
        print(f"Cache: removed {removed} oversized cached download(s) from {cache_path}.sqlite")
    session = _JitteredCachedSession(
        cache_path,
        base_expire=expire_after,
        allowable_codes=(200,),
        filter_fn=_should_cache_response,
        # If a refresh fails (network down, rate limit), serve the expired copy rather than erroring.
        stale_if_error=True,
    )
    if fresh_cache:
        session.cache.clear()
        from clients.edhrec_client import clear_missing_cache

        clear_missing_cache()
    return session


def ensure_scryfall_bulk(session: Optional[requests_cache.CachedSession] = None, *, fresh_cache: bool = False):
    """Load/refresh Scryfall oracle bulk data. Returns True on success."""
    from clients import scryfall_bulk

    sess = session or create_cached_session(fresh_cache=fresh_cache)
    return scryfall_bulk.ensure_loaded(sess)
