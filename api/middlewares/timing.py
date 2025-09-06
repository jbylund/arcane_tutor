from __future__ import annotations

import cProfile
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import falcon

logger = logging.getLogger(__name__)


class TimingMiddleware:
    """Middleware to log the duration, status, URL, and user agent for each request."""

    def process_request(self: TimingMiddleware, req: falcon.Request, resp: falcon.Response) -> None:
        req.context["_start_time"] = time.monotonic()

    def process_response(
        self: TimingMiddleware,
        req: falcon.Request,
        resp: falcon.Response,
        resource: object,
        req_succeeded: bool,
    ) -> None:
        start = req.context.get("_start_time")
        duration = time.monotonic() - start if start is not None else -1.0
        logger.info(
            "[timing] %.2f ms | %s | %s | %s",
            duration * 1000,
            resp.status,
            req.url,
            req.get_header("User-Agent", "-"),
        )


class ProfilingMiddleware:
    """Middleware to profile the request and response."""

    def __init__(self: ProfilingMiddleware) -> None:
        self.datadir = Path("/data/api/")

    def process_request(self: ProfilingMiddleware, req: falcon.Request, resp: falcon.Response) -> None:
        req.context["_profile"] = profile = cProfile.Profile()
        profile.enable()

    def process_response(
        self: ProfilingMiddleware,
        req: falcon.Request,
        resp: falcon.Response,
        resource: object,
        req_succeeded: bool,
    ) -> None:
        profile = req.context.get("_profile")
        if isinstance(profile, cProfile.Profile):
            profile.disable()
            profile_name = (req.url).rpartition("/")[-1]
            profile_name = profile_name.partition("?")[0]
            profile_id = int(1000 * time.monotonic())
            profile.dump_stats(self.datadir / f"profile_{profile_name}.{profile_id}.prof")
