from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from typing import cast as typecast

from cachetools import LRUCache

if TYPE_CHECKING:
    from collections.abc import MutableMapping

    import falcon

logger = logging.getLogger(__name__)

CacheKey = tuple[str, tuple[tuple, ...], tuple[tuple, ...]]


class CachingMiddleware:
    """Middleware to cache the request and response."""

    def __init__(self: CachingMiddleware, cache: MutableMapping | None = None) -> None:
        if cache is None:
            cache = LRUCache(maxsize=10_000)
        self.cache: MutableMapping[CacheKey, falcon.Response] = cache

    def _cache_key(self: CachingMiddleware, req: falcon.Request) -> CacheKey:
        cached_headers = [
            "ACCEPT-ENCODING",
        ]
        return (
            req.uri,
            tuple(sorted(req.params.items())),
            tuple(sorted({k: req.headers.get(k) for k in cached_headers}.items())),
        )

    def process_request(self: CachingMiddleware, req: falcon.Request, resp: falcon.Response) -> None:
        cache_key = self._cache_key(req)
        cached_value: falcon.Response | None = self.cache.get(cache_key)
        if cached_value is not None:
            if TYPE_CHECKING:
                cached_value = typecast("falcon.Response", cached_value)
            resp.complete = True
            resp.data = cached_value.data
            resp._headers.update(cached_value._headers)
            logger.info("Cache hit: %s / response_id: %d", req.url, id(resp))
            return
        logger.info("Cache miss: %s / %s", req.url, cache_key)

    def process_response(
        self: CachingMiddleware,
        req: falcon.Request,
        resp: falcon.Response,
        resource: object,
        req_succeeded: bool,
    ) -> None:
        cache_key = self._cache_key(req)
        cached_val = self.cache.get(cache_key)
        if cached_val is None:
            resp.complete = True
            self.cache[cache_key] = resp
            logger.info("Cache updated: %s / %s", req.url, cache_key)
