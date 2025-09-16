"""Client for the Scryfall cache service."""

import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)


class ScryfallCacheClient:
    """Client for accessing Scryfall API through the cache service."""

    def __init__(self, cache_service_url: str | None = None) -> None:
        """Initialize the cache client.

        Args:
            cache_service_url: URL of the cache service. If None, uses environment variable
                              CACHE_SERVICE_URL or defaults to http://cacheservice:8081
        """
        if cache_service_url is None:
            cache_service_url = os.environ.get(
                "CACHE_SERVICE_URL",
                "http://cacheservice:8081",
            )

        self.cache_service_url = cache_service_url.rstrip("/")
        self.session = requests.Session()

        logger.info("Initialized ScryfallCacheClient with URL: %s", self.cache_service_url)

    def get(self, url: str, max_age_seconds: int = 3600, timeout: int = 30) -> dict[str, Any]:
        """Get data from Scryfall API through the cache service.

        Args:
            url: The Scryfall URL to fetch
            max_age_seconds: Maximum age of cached data in seconds
            timeout: Request timeout in seconds

        Returns:
            JSON response from Scryfall (cached or fresh)

        Raises:
            requests.RequestException: If the request fails
        """
        cache_url = f"{self.cache_service_url}/cache"
        params = {
            "url": url,
            "max_age": max_age_seconds,
        }

        logger.debug("Requesting %s through cache service", url)

        try:
            response = self.session.get(cache_url, params=params, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as e:
            logger.error("Cache service request failed for %s: %s", url, e)
            # If cache service is unavailable or returns non-JSON, fallback to direct request
            logger.warning("Falling back to direct Scryfall request")
            fallback_response = self.session.get(url, timeout=timeout)
            fallback_response.raise_for_status()

            # Try to return JSON, but if it's not JSON, raise the original error
            try:
                return fallback_response.json()
            except ValueError:
                # If the fallback response is also not JSON, re-raise the original error
                raise e from None

    def clear_cache(self, url: str | None = None) -> dict[str, Any]:
        """Clear cache entries.

        Args:
            url: Specific URL to clear, or None to clear all cache

        Returns:
            Response from cache service
        """
        cache_url = f"{self.cache_service_url}/cache"
        data = {"url": url} if url else {}

        response = self.session.post(cache_url, json=data)
        response.raise_for_status()
        return response.json()

    def get_cache_stats(self) -> dict[str, Any]:
        """Get cache statistics.

        Returns:
            Cache statistics from the cache service
        """
        stats_url = f"{self.cache_service_url}/cache/stats"

        response = self.session.get(stats_url)
        response.raise_for_status()
        return response.json()
