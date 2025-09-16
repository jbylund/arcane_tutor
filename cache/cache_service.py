"""Scryfall read-through cache service."""

import base64
import json
import logging
import pathlib
import shutil
import time
from typing import Any
from urllib.parse import urlparse

import falcon
import requests
from ratelimit import limits, sleep_and_retry

logger = logging.getLogger("cache_service")

# Rate limiting: Scryfall allows 10 requests per second
RATE_LIMIT_CALLS = 10
RATE_LIMIT_PERIOD = 1  # second


class CacheService:
    """Read-through cache service for Scryfall API requests."""

    def __init__(self, cache_dir: str = "/data/cache") -> None:
        """Initialize the cache service.

        Args:
            cache_dir: Base directory for cache storage
        """
        self.cache_dir = pathlib.Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Create reusable requests session with appropriate headers
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "ScryfallfOS-Cache/1.0 (https://github.com/jbylund/scryfallos)",
        })

        logger.info("Cache service initialized with cache_dir: %s", self.cache_dir)

    def _get_cache_path(self, url: str) -> pathlib.Path:
        """Generate cache file path from URL using directory structure host/base64_encoded_url.

        Args:
            url: The URL to cache

        Returns:
            Path to the cache file
        """
        parsed = urlparse(url)
        host = parsed.netloc

        # Encode the full URL as base64 for the filename
        url_encoded = base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")

        # Create directory structure: host/base64_encoded_url.json
        return self.cache_dir / host / f"{url_encoded}.json"

    @sleep_and_retry
    @limits(calls=RATE_LIMIT_CALLS, period=RATE_LIMIT_PERIOD)
    def _fetch_from_scryfall(self, url: str) -> dict[str, Any]:
        """Fetch data from Scryfall API with rate limiting.

        Args:
            url: The URL to fetch

        Returns:
            JSON response from Scryfall

        Raises:
            requests.RequestException: If the request fails
        """
        logger.info("Fetching from Scryfall: %s", url)
        response = self._session.get(url, timeout=30)
        response.raise_for_status()

        # Only cache JSON responses
        content_type = response.headers.get("content-type", "").lower()
        if "application/json" not in content_type:
            msg = f"Non-JSON response received: {content_type}"
            raise ValueError(msg)

        return response.json()

    def get(self, url: str, max_age_seconds: int = 3600) -> dict[str, Any]:
        """Get data from cache or fetch from Scryfall.

        Args:
            url: The URL to get data for
            max_age_seconds: Maximum age of cached data in seconds

        Returns:
            JSON data from cache or Scryfall
        """
        cache_path = self._get_cache_path(url)

        # Check if cache file exists and is not too old
        if cache_path.exists():
            stat = cache_path.stat()
            age = time.time() - stat.st_mtime

            if age < max_age_seconds:
                logger.info("Cache hit: %s (age: %.1fs)", url, age)
                try:
                    with cache_path.open() as f:
                        return json.load(f)
                except (json.JSONDecodeError, OSError) as e:
                    logger.warning("Failed to read cache file %s: %s", cache_path, e)
                    # Continue to fetch from Scryfall
            else:
                logger.info("Cache expired: %s (age: %.1fs)", url, age)
        else:
            logger.info("Cache miss: %s", url)

        # Fetch from Scryfall
        data = self._fetch_from_scryfall(url)

        # Store in cache
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with cache_path.open("w") as f:
                json.dump(data, f, indent=2, sort_keys=True)
            logger.info("Cached response: %s", cache_path)
        except OSError as e:
            logger.warning("Failed to write cache file %s: %s", cache_path, e)

        return data


class CacheResource:
    """Falcon resource for the cache service API."""

    def __init__(self) -> None:
        """Initialize the cache resource."""
        self.cache_service = CacheService()

    def on_get(self, req: falcon.Request, resp: falcon.Response) -> None:
        """Handle GET requests to proxy and cache Scryfall API calls.

        Query parameters:
        - url: The Scryfall URL to fetch (required)
        - max_age: Maximum age of cached data in seconds (optional, default 3600)
        """
        url = req.get_param("url", required=True)
        max_age = req.get_param_as_int("max_age", default=3600)

        # Validate that URL is from allowed domains
        allowed_hosts = {"api.scryfall.com", "scryfall.com"}
        parsed = urlparse(url)
        if parsed.netloc not in allowed_hosts:
            raise falcon.HTTPBadRequest(
                title="Invalid URL",
                description=f"Only URLs from {allowed_hosts} are allowed",
            )

        try:
            data = self.cache_service.get(url, max_age_seconds=max_age)
            resp.media = data
        except requests.RequestException as e:
            logger.error("Request failed for %s: %s", url, e)
            raise falcon.HTTPBadGateway(
                title="Upstream Error",
                description=f"Failed to fetch data from Scryfall: {e}",
            ) from e
        except ValueError as e:
            logger.error("Invalid response for %s: %s", url, e)
            raise falcon.HTTPBadRequest(
                title="Invalid Response",
                description=str(e),
            ) from e

    def on_post(self, req: falcon.Request, resp: falcon.Response) -> None:
        """Handle POST requests to clear cache entries.

        Request body should contain:
        - url: The URL to clear from cache (optional, clears all if not provided)
        """
        try:
            data = req.get_media()
            url = data.get("url") if data else None
        except (ValueError, TypeError):
            url = None

        if url:
            # Clear specific URL from cache
            cache_path = self.cache_service._get_cache_path(url)
            if cache_path.exists():
                cache_path.unlink()
                resp.media = {"message": f"Cleared cache for {url}"}
            else:
                resp.media = {"message": f"No cache entry found for {url}"}
        # Clear all cache (remove all files in cache directory)
        elif self.cache_service.cache_dir.exists():
            shutil.rmtree(self.cache_service.cache_dir)
            self.cache_service.cache_dir.mkdir(parents=True, exist_ok=True)
            resp.media = {"message": "Cleared all cache"}
        else:
            resp.media = {"message": "Cache directory does not exist"}

    def on_get_stats(self, _req: falcon.Request, resp: falcon.Response) -> None:
        """Get cache statistics."""
        cache_dir = self.cache_service.cache_dir

        if not cache_dir.exists():
            resp.media = {
                "cache_entries": 0,
                "total_size_bytes": 0,
                "hosts": [],
            }
            return

        # Count files and calculate total size
        cache_entries = 0
        total_size = 0
        hosts = set()

        for host_dir in cache_dir.iterdir():
            if host_dir.is_dir():
                hosts.add(host_dir.name)
                for cache_file in host_dir.glob("*.json"):
                    if cache_file.is_file():
                        cache_entries += 1
                        total_size += cache_file.stat().st_size

        resp.media = {
            "cache_entries": cache_entries,
            "total_size_bytes": total_size,
            "hosts": sorted(hosts),
        }


def create_app() -> falcon.App:
    """Create and configure the Falcon application."""
    app = falcon.App()

    cache_resource = CacheResource()
    app.add_route("/cache", cache_resource)
    app.add_route("/cache/stats", cache_resource, suffix="stats")

    return app


if __name__ == "__main__":
    # For development/testing
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        # Simple test
        import tempfile
        with tempfile.TemporaryDirectory() as temp_dir:
            service = CacheService(temp_dir)
            test_url = "https://api.scryfall.com/bulk-data"

            # This would fail without network access, but shows the structure
            cache_path = service._get_cache_path(test_url)
    else:
        pass
