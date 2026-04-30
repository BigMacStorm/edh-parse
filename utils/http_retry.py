import time
from typing import Any, Callable, Optional, Tuple

import requests


def get_with_retry(
    session: Any,
    url: str,
    params: Optional[dict] = None,
    timeout: int = 5,
    max_retries: int = 5,
    rate_limited_status: int = 429,
    on_rate_limited: Optional[Callable[[int, int, int], None]] = None,
) -> Tuple[Optional[requests.Response], Optional[BaseException]]:
    """
    GET with exponential backoff on rate-limits (default: HTTP 429).

    Returns:
      (response, None) on success
      (None, exception) on failure
    """

    last_err: Optional[BaseException] = None
    for attempt in range(max_retries):
        try:
            response = session.get(url, timeout=timeout, params=params)
            if response.status_code == rate_limited_status:
                wait = 2 ** (attempt + 1)
                if on_rate_limited is not None:
                    on_rate_limited(attempt, max_retries, wait)
                if attempt < max_retries - 1:
                    time.sleep(wait)
                    continue
            response.raise_for_status()
            return response, None
        except requests.exceptions.RequestException as err:
            last_err = err
            status = getattr(getattr(err, "response", None), "status_code", None)
            if status == rate_limited_status:
                wait = 2 ** (attempt + 1)
                if on_rate_limited is not None:
                    on_rate_limited(attempt, max_retries, wait)
                if attempt < max_retries - 1:
                    time.sleep(wait)
                    continue
            return None, last_err

    return None, last_err

