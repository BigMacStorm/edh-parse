"""Warm the HTTP cache for a batch of commanders using a few worker threads.

The summary build asks EDHREC for six pages per commander (commander page + average deck
for Normal/Budget/Expensive) plus a combos page at render time. Doing that one request at a
time with a sleep after each is the dominant cost of a cold run. This fetches every page that
isn't already cached concurrently, behind a single shared rate limiter, so the later
(serial) build only ever reads from the cache.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Iterable, List

from clients.edhrec_client import EdhrecClient
from utils.progress_utils import progress_bar

EDHREC_BASE = "https://json.edhrec.com/pages"
DEFAULT_WORKERS = 4
# Global gap between uncached requests (~6/s overall), regardless of worker count.
DEFAULT_MIN_INTERVAL = 0.17


def edhrec_urls_for_commander(base_name: str, include_combos: bool = True) -> List[str]:
    """Every EDHREC URL the summary build requests for one commander slug.

    Must stay in sync with Deck.lookup_normal/lookup_budget/lookup_expensive,
    Deck._fetch_average_deck_archidekt and Collection._fetch_combos.
    """
    urls = [
        f"{EDHREC_BASE}/commanders/{base_name}.json",
        f"{EDHREC_BASE}/commanders/{base_name}/budget.json",
        f"{EDHREC_BASE}/commanders/{base_name}/expensive.json",
        f"{EDHREC_BASE}/average-decks/{base_name}.json",
        f"{EDHREC_BASE}/average-decks/{base_name}/budget.json",
        f"{EDHREC_BASE}/average-decks/{base_name}/expensive.json",
    ]
    if include_combos:
        urls.append(f"{EDHREC_BASE}/combos/{base_name}.json")
    return urls


class _RateLimiter:
    def __init__(self, interval: float):
        self.interval = interval
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + self.interval
        delay = start - now
        if delay > 0:
            time.sleep(delay)

    def penalize(self, seconds: float) -> None:
        """Push every worker's next request back after a 429."""
        with self._lock:
            self._next = max(self._next, time.monotonic() + seconds)


def prefetch_urls(
    session,
    urls: Iterable[str],
    *,
    workers: int = DEFAULT_WORKERS,
    min_interval: float = DEFAULT_MIN_INTERVAL,
    label: str = "EDHREC pages",
) -> Dict[str, float]:
    """Fetch every not-yet-cached URL concurrently behind one shared rate limiter. Returns stats."""
    limiter = _RateLimiter(min_interval)
    client = EdhrecClient(session, sleep_seconds=0.0, on_rate_limited=limiter.penalize)

    todo = []
    for url in dict.fromkeys(urls):
        if client.is_known_missing(url) or client.is_cached(url):
            continue
        todo.append(url)

    stats = {"to_fetch": len(todo), "fetched": 0, "missing": 0, "errors": 0, "seconds": 0.0}
    if not todo or workers < 1:
        return stats

    def _fetch(url: str) -> str:
        limiter.wait()
        data, err = client.get_json(url, timeout=10)
        if data is not None:
            return "fetched"
        status = getattr(getattr(err, "response", None), "status_code", None)
        return "missing" if status == 404 else "error"

    started = time.monotonic()
    done = 0
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="edhrec")
    try:
        with progress_bar(len(todo), initial_value=0) as bar:
            bar.update(0, info=f"Prefetching {len(todo)} {label}")
            futures = [pool.submit(_fetch, url) for url in todo]
            for fut in as_completed(futures):
                try:
                    outcome = fut.result()
                except Exception:
                    outcome = "error"
                stats[{"fetched": "fetched", "missing": "missing"}.get(outcome, "errors")] += 1
                done += 1
                bar.update(done, info=f"Prefetching {label} ({done}/{len(todo)})")
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    stats["seconds"] = round(time.monotonic() - started, 1)
    return stats


def prefetch_edhrec(
    session,
    base_names: Iterable[str],
    *,
    workers: int = DEFAULT_WORKERS,
    min_interval: float = DEFAULT_MIN_INTERVAL,
    include_combos: bool = True,
) -> Dict[str, float]:
    """Fetch all not-yet-cached EDHREC pages for these commander slugs. Returns simple stats."""
    seen = set()
    slugs = []
    for name in base_names:
        slug = (name or "").strip()
        if slug and slug not in seen:
            seen.add(slug)
            slugs.append(slug)

    urls = [u for slug in slugs for u in edhrec_urls_for_commander(slug, include_combos=include_combos)]
    stats = prefetch_urls(session, urls, workers=workers, min_interval=min_interval)
    stats["commanders"] = len(slugs)
    return stats
