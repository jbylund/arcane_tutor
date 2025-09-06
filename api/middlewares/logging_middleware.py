from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import falcon

logger = logging.getLogger(__name__)

class LoggingMiddleware:
    """Middleware to add some simple logging the request and response."""

    def __init__(self: LoggingMiddleware, middleware_name: str) -> None:
        self.middleware_name = middleware_name

    def process_request(self: LoggingMiddleware, req: falcon.Request, resp: falcon.Response) -> None:
        logger.info("[%s] Request received: %s", self.middleware_name, req.uri)

    def process_response(
        self: LoggingMiddleware,
        req: falcon.Request,
        resp: falcon.Response,
        resource: object,
        req_succeeded: bool,
    ) -> None:
        logger.info("[%s] Response sent: %s", self.middleware_name, req.uri)
