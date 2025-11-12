"""Caching middleware for Falcon API responses."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from cachetools import LRUCache

from api.settings import settings

if TYPE_CHECKING:
    from collections.abc import MutableMapping

    import falcon

logger = logging.getLogger(__name__)

CacheKey = tuple[str, tuple[tuple, ...], tuple[tuple, ...]]


def serialize_response(resp: falcon.Response) -> dict[str, Any]:
    """Serialize the response to a bytes object."""
    return {
        "data": resp.data,
        "media": resp.media,
        "headers": resp._headers,
        "status": resp.status,
    }

class CachingMiddleware:
    """Middleware to cache the request and response."""

    def __init__(self: CachingMiddleware, cache: MutableMapping | None = None) -> None:
        """Initialize the caching middleware with an optional cache instance.

        Args:
            cache: Optional cache instance. If None, creates an LRUCache with maxsize 10,000.
        """
        if cache is None:
            cache = LRUCache(maxsize=10_000)
        self.cache: MutableMapping[CacheKey, falcon.Response] = cache

    def _cache_key(self: CachingMiddleware, req: falcon.Request) -> CacheKey:
        cached_headers = [
            "ACCEPT-ENCODING",
        ]
        return (
            req.relative_uri,
            tuple(sorted(req.params.items())),
            tuple(sorted({k: req.headers.get(k) for k in cached_headers}.items())),
        )

    def process_request(self: CachingMiddleware, req: falcon.Request, resp: falcon.Response) -> None:
        """Process incoming request and check for cached response.

        Args:
            req: The incoming request.
            resp: The response object to populate if cache hit.
        """
        if not settings.enable_cache:
            return

        cache_key = self._cache_key(req)
        cached_value = self.cache.get(cache_key)
        if cached_value is not None:
            resp.complete = True
            resp.data = cached_value["data"]
            resp.media = cached_value["media"]
            resp.status = cached_value["status"]
            resp._headers.update(cached_value["headers"])
            logger.info("Cache hit: %s / %s", req.relative_uri, resp.status)
            return
        logger.info("Cache miss: %s / %s", req.relative_uri, cache_key)

    def process_response(
        self: CachingMiddleware,
        req: falcon.Request,
        resp: falcon.Response,
        resource: object,
        req_succeeded: bool,
    ) -> None:
        """Process outgoing response and cache it if not already cached.

        Args:
            req: The request that generated this response.
            resp: The response to potentially cache.
            resource: The resource that handled the request (unused).
            req_succeeded: Whether the request was successful (unused).
        """
        if not settings.enable_cache:
            return

        del resource, req_succeeded
        cache_key = self._cache_key(req)
        if cache_key in self.cache:
            pass  # was already present
        else:
            self.cache[cache_key] = serialize_response(resp)
            logger.info("Cache updated: %s / %s", req.relative_uri, cache_key)
