import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

import requests

from utils.http_retry import get_with_retry

# EDHREC returns 404 for pages that don't exist (e.g. no Budget page for a rarely-built
# commander). The HTTP cache only stores 200s, so without this every run would
# re-request those URLs. Remembered for a few days, then retried.
DEFAULT_MISSING_CACHE_PATH = "cache/edhrec_missing.json"
MISSING_TTL_SECONDS = 3 * 24 * 3600

_missing_lock = threading.Lock()
_missing_state: Dict[str, Dict[str, float]] = {}


def _load_missing(path: str) -> Dict[str, float]:
    with _missing_lock:
        if path not in _missing_state:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                _missing_state[path] = data if isinstance(data, dict) else {}
            except (OSError, ValueError):
                _missing_state[path] = {}
        return _missing_state[path]


def _save_missing(path: str) -> None:
    """Caller must hold _missing_lock."""
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_missing_state.get(path, {}), f)
        os.replace(tmp, path)
    except OSError:
        pass


def _is_s3_access_denied(response) -> bool:
    """True for the S3 'AccessDenied' XML body json.edhrec.com returns for pages that don't exist."""
    if response is None:
        return False
    try:
        ctype = (response.headers.get("Content-Type") or "").lower()
        head = (response.text or "")[:400]
    except Exception:
        return False
    return "xml" in ctype or "<Code>AccessDenied</Code>" in head


def clear_missing_cache(path: str = DEFAULT_MISSING_CACHE_PATH) -> None:
    with _missing_lock:
        _missing_state[path] = {}
        _save_missing(path)


class EdhrecClient:
    """
    Centralized EDHREC HTTP access with:
    - 429 exponential backoff (via utils.http_retry.get_with_retry)
    - a small post-request delay when the response is not from cache
    - a short-lived record of 404 pages so they aren't re-requested on every run
    """

    def __init__(
        self,
        session: Any,
        sleep_seconds: float = 0.25,
        on_rate_limited: Optional[Callable[[int], None]] = None,
        missing_cache_path: Optional[str] = DEFAULT_MISSING_CACHE_PATH,
    ):
        self.session = session
        self.sleep_seconds = sleep_seconds
        self.on_rate_limited = on_rate_limited
        self.missing_cache_path = missing_cache_path

    def is_known_missing(self, url: str) -> bool:
        if not self.missing_cache_path:
            return False
        ts = _load_missing(self.missing_cache_path).get(url)
        return bool(ts) and (time.time() - float(ts)) < MISSING_TTL_SECONDS

    def _record_missing(self, url: str) -> None:
        if not self.missing_cache_path:
            return
        data = _load_missing(self.missing_cache_path)
        with _missing_lock:
            data[url] = time.time()
            _save_missing(self.missing_cache_path)

    def is_cached(self, url: str) -> bool:
        """True if a fresh copy is in the HTTP cache (so fetching it costs no network request).

        Asks the session itself (``only_if_cached``) so the cache key matches a real request
        exactly, whatever TLS/proxy settings are in the environment.
        """
        try:
            response = self.session.get(url, only_if_cached=True, timeout=5)
            return response.status_code == 200 and bool(getattr(response, "from_cache", False))
        except Exception:
            return False

    def get_json(
        self, url: str, timeout: int = 10, max_retries: int = 5
    ) -> Tuple[Optional[Any], Optional[BaseException]]:
        if self.is_known_missing(url):
            return None, requests.exceptions.HTTPError(f"404 Not Found (remembered): {url}")

        def _log_rate_limited(_attempt: int, _max_attempts: int, wait: int) -> None:
            print(f"Rate limited by EDHREC; backing off {wait}s...")
            if self.on_rate_limited is not None:
                self.on_rate_limited(wait)

        response, err = get_with_retry(
            self.session,
            url,
            params=None,
            timeout=timeout,
            max_retries=max_retries,
            on_rate_limited=_log_rate_limited,
        )
        if err or response is None:
            err_response = getattr(err, "response", None)
            status = getattr(err_response, "status_code", None)
            if status == 404 or (status == 403 and _is_s3_access_denied(err_response)):
                # json.edhrec.com is an S3 bucket: missing pages come back as 403 AccessDenied (XML).
                self._record_missing(url)
            return None, err
        if not getattr(response, "from_cache", True):
            time.sleep(self.sleep_seconds)
        try:
            return response.json(), None
        except Exception as json_err:  # pragma: no cover (network dependent)
            return None, json_err


__all__ = ["EdhrecClient", "clear_missing_cache"]
