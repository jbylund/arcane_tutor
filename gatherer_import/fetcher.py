"""Fetcher module for retrieving card data from Gatherer/Scryfall."""

from __future__ import annotations

import logging
import time
from typing import Any

import orjson
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

# HTTP status codes
HTTP_NOT_FOUND = 404
HTTP_OK = 200

# Rate limiting - Scryfall allows 10 requests per second, we'll be conservative
REQUEST_DELAY = 0.1  # 100ms between requests


class GathererFetcher:
    """Fetcher class for retrieving Magic: The Gathering card data.

    This class handles fetching card data from Scryfall API organized by set.
    It includes rate limiting and error handling.
    """

    def __init__(self: GathererFetcher, *, base_url: str = "https://api.scryfall.com") -> None:
        """Initialize the GathererFetcher.

        Args:
        ----
            base_url: Base URL for the Scryfall API. Defaults to the official Scryfall API.

        """
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "ArcaneGathererImport/1.0",
            "Accept": "application/json",
        })
        self._last_request_time = 0.0

    def _rate_limit(self: GathererFetcher) -> None:
        """Apply rate limiting to API requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < REQUEST_DELAY:
            time.sleep(REQUEST_DELAY - elapsed)
        self._last_request_time = time.time()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def _make_request(self: GathererFetcher, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Make a rate-limited HTTP request with retry logic.

        Args:
        ----
            url: The URL to request
            params: Optional query parameters

        Returns:
        -------
            The parsed JSON response

        Raises:
        ------
            requests.RequestException: If the request fails after retries

        """
        self._rate_limit()
        response = self.session.get(url, params=params, timeout=30)
        response.raise_for_status()
        return orjson.loads(response.content)

    def fetch_set_info(self: GathererFetcher, set_code: str) -> dict[str, Any]:
        """Fetch information about a specific set.

        Args:
        ----
            set_code: The three-letter set code (e.g., "DOM", "WAR")

        Returns:
        -------
            Dictionary containing set information including name, release date, card count, etc.

        Raises:
        ------
            requests.RequestException: If the request fails
            ValueError: If the set code is not found

        """
        url = f"{self.base_url}/sets/{set_code}"
        try:
            data = self._make_request(url)
            logger.info(f"Fetched info for set {set_code}: {data.get('name', 'Unknown')}")
            return data
        except requests.RequestException as e:
            if hasattr(e, "response") and e.response and e.response.status_code == HTTP_NOT_FOUND:
                msg = f"Set code '{set_code}' not found"
                raise ValueError(msg) from e
            raise

    def fetch_set(self: GathererFetcher, set_code: str, *, include_extras: bool = False) -> list[dict[str, Any]]:
        """Fetch all cards from a specific set.

        Args:
        ----
            set_code: The three-letter set code (e.g., "DOM", "WAR")
            include_extras: Whether to include extra cards (tokens, emblems, etc.)

        Returns:
        -------
            List of card dictionaries containing all card data

        Raises:
        ------
            requests.RequestException: If the request fails
            ValueError: If the set code is not found

        """
        # Build search query for the set
        # We filter for paper-format cards and exclude funny sets
        query_parts = [f"e:{set_code}", "game:paper"]

        if not include_extras:
            query_parts.append("not:extra")

        query = " ".join(query_parts)

        url = f"{self.base_url}/cards/search"
        params = {
            "q": query,
            "order": "set",
            "unique": "prints",
        }

        all_cards = []
        page_num = 0

        logger.info(f"Fetching cards for set {set_code}...")

        try:
            while True:
                page_num += 1
                logger.info(f"Fetching page {page_num} for set {set_code}...")

                data = self._make_request(url, params if params else None)

                if "data" not in data:
                    break

                # Extract cards from current page
                page_cards = data["data"]
                all_cards.extend(page_cards)
                logger.info(f"Retrieved {len(page_cards)} cards from page {page_num}")

                # Check if there are more pages
                if not data.get("has_more", False):
                    break

                # Get next page URL
                next_page = data.get("next_page")
                if not next_page:
                    break

                # Update URL and clear params for pagination
                url = next_page
                params = None

            logger.info(f"Successfully fetched {len(all_cards)} cards from set {set_code}")
            return all_cards

        except requests.RequestException as e:
            if hasattr(e, "response") and e.response and e.response.status_code == HTTP_NOT_FOUND:
                logger.warning(f"No cards found for set {set_code}")
                return []
            logger.error(f"Error fetching cards for set {set_code}: {e}")
            raise

    def fetch_all_sets(self: GathererFetcher) -> list[dict[str, Any]]:
        """Fetch information about all available sets.

        Returns:
        -------
            List of set dictionaries containing set information

        Raises:
        ------
            requests.RequestException: If the request fails

        """
        url = f"{self.base_url}/sets"
        all_sets = []

        logger.info("Fetching all sets...")

        try:
            while url:
                data = self._make_request(url)

                if "data" in data:
                    all_sets.extend(data["data"])

                # Check for pagination
                url = data.get("next_page") if data.get("has_more", False) else None

            logger.info(f"Successfully fetched {len(all_sets)} sets")
            return all_sets

        except requests.RequestException as e:
            logger.error(f"Error fetching sets: {e}")
            raise
