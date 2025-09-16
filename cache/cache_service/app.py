"""Application factory for the cache service."""

from __future__ import annotations

import falcon
import logging

from .cache_resource import CacheResource
from .middlewares import LoggingMiddleware

logger = logging.getLogger(__name__)


def create_app() -> falcon.App:
    """Create and configure the Falcon application."""
    logging.basicConfig(level=logging.INFO)
    app = falcon.App(
        middleware=[
            LoggingMiddleware(),
        ],
    )
    cache_resource = CacheResource()
    app.add_sink(cache_resource._handle, prefix="/")
    return app
