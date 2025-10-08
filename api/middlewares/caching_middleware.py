"""Caching middleware for Falcon API responses."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from typing import cast as typecast

from cachetools import TTLCache

if TYPE_CHECKING:
    from collections.abc import MutableMapping

    import falcon

logger = logging.getLogger(__name__)

CacheKey = tuple[str, tuple[tuple, ...], tuple[tuple, ...]]


class CachingMiddleware:
    """Middleware to cache the request and response."""

    def __init__(self: CachingMiddleware, cache: MutableMapping | None = None) -> None:
        """Initialize the caching middleware with an optional cache instance.

        Args:
            cache: Optional cache instance. If None, creates an LRUCache with maxsize 10,000.
        """
        cache = cache or TTLCache(maxsize=10_000, ttl=60)
        self.cache: MutableMapping[CacheKey, falcon.Response] = cache
        self.uncached_paths = {
            "/db_ready",
            "/get_pid",
        }

    def _cache_key(self: CachingMiddleware, req: falcon.Request) -> CacheKey:
        cached_headers = [
            "ACCEPT-ENCODING",
        ]
        return (
            req.path,
            tuple(sorted(req.params.items())),
            tuple(sorted({k: req.headers.get(k) for k in cached_headers}.items())),
        )

    def process_request(self: CachingMiddleware, req: falcon.Request, resp: falcon.Response) -> None:
        """Process incoming request and check for cached response.

        Args:
            req: The incoming request.
            resp: The response object to populate if cache hit.
        """
        if req.path in self.uncached_paths:
            req.context["do_not_cache"] = True
            logger.info("Uncached path: %s", req.path)
            return
        cache_key = self._cache_key(req)
        cached_value: falcon.Response | None = self.cache.get(cache_key)
        if cached_value is not None:
            if TYPE_CHECKING:
                cached_value = typecast("falcon.Response", cached_value)
            resp.complete = True
            resp.data = cached_value.data
            resp.media = cached_value.media
            resp._headers.update(cached_value._headers)
            resp.status = cached_value.status
            logger.info("Cache hit: %s / %s response_id: %d", req.path, resp.status, id(resp))
            return
        logger.info("Cache miss: %s / %s", req.path, cache_key)

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
        if req.context.get("do_not_cache"):
            return
        del resource, req_succeeded
        cache_key = self._cache_key(req)
        cached_val = self.cache.get(cache_key)
        if cached_val is None:
            resp.complete = True
            self.cache[cache_key] = resp
            logger.info("Cache updated: %s / %s", req.path, cache_key)
