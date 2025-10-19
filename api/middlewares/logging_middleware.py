"""Logging middleware for Falcon API requests and responses."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import falcon

logger = logging.getLogger(__name__)


class LoggingMiddleware:
    """Middleware to add some simple logging the request and response."""

    def __init__(self: LoggingMiddleware, middleware_name: str) -> None:
        """Initialize the logging middleware.

        Args:
            middleware_name: Name to use in log messages for this middleware instance.
        """
        self.middleware_name = middleware_name

    def process_request(self: LoggingMiddleware, req: falcon.Request, resp: falcon.Response) -> None:
        """Log incoming request.

        Args:
            req: The incoming request.
            resp: The response object (unused).
        """
        del resp
        logger.info("[%s] Request received: %s", self.middleware_name, req.relative_uri)

    def process_response(
        self: LoggingMiddleware,
        req: falcon.Request,
        resp: falcon.Response,
        resource: object,
        req_succeeded: bool,
    ) -> None:
        """Log outgoing response.

        Args:
            req: The request that generated this response.
            resp: The response object (unused).
            resource: The resource that handled the request (unused).
            req_succeeded: Whether the request was successful (unused).
        """
        del resp, resource, req_succeeded
        logger.info("[%s] Response sent: %s", self.middleware_name, req.relative_uri)
