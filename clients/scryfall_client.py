import time
from typing import Any, Optional, Tuple

from clients import scryfall_bulk

from utils.http_retry import get_with_retry


class ScryfallClient:
    """
    Centralized Scryfall access with:
    - oracle_cards bulk lookups (via scryfall_bulk)
    - API fallback when bulk doesn't have the answer cached/indexed
    - retry/backoff for HTTP 429 (via utils.http_retry.get_with_retry)
    - a small sleep between API requests to stay under rate limits
    """

    def __init__(self, session: Any, sleep_seconds: float = 0.15):
        self.session = session
        self.sleep_seconds = sleep_seconds

    def _ensure_bulk(self) -> None:
        scryfall_bulk.ensure_loaded(self.session)

    def get_card_by_name_exact(
        self, card_name: str, timeout: int = 5, max_retries: int = 5
    ) -> Tuple[Optional[Any], Optional[BaseException]]:
        if not card_name:
            return None, None
        self._ensure_bulk()
        card_found = scryfall_bulk.get_card_by_name(card_name)
        if card_found is not None:
            return card_found, None

        url = "https://api.scryfall.com/cards/named"
        params = {"exact": card_name}
        response, err = get_with_retry(
            self.session,
            url,
            params=params,
            timeout=timeout,
            max_retries=max_retries,
        )
        if err or response is None:
            return None, err
        time.sleep(self.sleep_seconds)
        return response.json(), None

    def get_card_by_id(
        self, card_id: str, timeout: int = 5, max_retries: int = 5
    ) -> Tuple[Optional[Any], Optional[BaseException]]:
        if not card_id:
            return None, None
        self._ensure_bulk()
        card_found = scryfall_bulk.get_card_by_id(card_id)
        if card_found is not None:
            return card_found, None

        url = f"https://api.scryfall.com/cards/{card_id}"
        response, err = get_with_retry(
            self.session,
            url,
            params=None,
            timeout=timeout,
            max_retries=max_retries,
        )
        if err or response is None:
            return None, err
        time.sleep(self.sleep_seconds)
        return response.json(), None

    def fetch_alternate_names(
        self, card_data: dict, timeout: int = 5, max_retries: int = 5
    ) -> Tuple[Optional[set], Optional[BaseException]]:
        if not card_data:
            return None, None
        prints_search_uri = card_data.get("prints_search_uri")
        if not prints_search_uri:
            return None, None

        response, err = get_with_retry(
            self.session,
            prints_search_uri,
            params=None,
            timeout=timeout,
            max_retries=max_retries,
        )
        if err or response is None:
            return None, err

        time.sleep(self.sleep_seconds)
        prints = response.json()
        out = set()
        for printing in prints.get("data", []):
            name = printing.get("name")
            if name:
                out.add(name)
        return out, None


__all__ = ["ScryfallClient"]

