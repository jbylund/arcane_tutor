"""Middleware classes for the cache service."""

from __future__ import annotations

import falcon
import logging

logger = logging.getLogger(__name__)


class LoggingMiddleware:
    """Middleware to log the request and response."""

    def process_request(self, req: falcon.Request, resp: falcon.Response) -> None:
        """Log the request."""
        logger.info("Request: %s", req.uri)
