import time
from typing import Any, Optional, Tuple

from utils.http_retry import get_with_retry


class EdhrecClient:
    """
    Centralized EDHREC HTTP access with:
    - 429 exponential backoff (via utils.http_retry.get_with_retry)
    - a small post-request delay when the response is not from cache
    """

    def __init__(self, session: Any, sleep_seconds: float = 0.25):
        self.session = session
        self.sleep_seconds = sleep_seconds

    def get_json(
        self, url: str, timeout: int = 10, max_retries: int = 5
    ) -> Tuple[Optional[Any], Optional[BaseException]]:
        def _log_rate_limited(_attempt: int, _max_attempts: int, wait: int) -> None:
            print(f"Rate limited by EDHREC; backing off {wait}s...")

        response, err = get_with_retry(
            self.session,
            url,
            params=None,
            timeout=timeout,
            max_retries=max_retries,
            on_rate_limited=_log_rate_limited,
        )
        if err or response is None:
            return None, err
        if not getattr(response, "from_cache", True):
            time.sleep(self.sleep_seconds)
        try:
            return response.json(), None
        except Exception as json_err:  # pragma: no cover (network dependent)
            return None, json_err


__all__ = ["EdhrecClient"]

