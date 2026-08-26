"""Early rejection for /search queries that exceed public size or nesting bounds."""

from __future__ import annotations

import logging

import falcon

from api.parsing.query_budget import (
    QueryBudgetExceeded,
    bounded_query_log_context,
    check_search_param_lengths,
)

logger = logging.getLogger(__name__)


class SearchBudgetMiddleware:
    """Reject over-budget ``/search`` byte lengths before parsing or the search handler.

    Runs ahead of ``QueryLogMiddleware`` and ``CachingMiddleware`` so rejected requests
    skip parsing and never reach ``_search``. Downstream ``process_response`` hooks may
    still record or cache the 400.
    """

    def process_request(self, req: falcon.Request, resp: falcon.Response) -> None:
        """Validate ``q`` and ``query`` byte lengths for /search requests."""
        if req.path.strip("/") != "search":
            return
        try:
            check_search_param_lengths(req.params)
        except QueryBudgetExceeded as exc:
            q_value = req.params.get("q")
            query_value = req.params.get("query")
            sample = q_value if q_value is not None else query_value if query_value is not None else ""
            if isinstance(sample, list):
                sample = sample[0] if sample else ""
            log_ctx = bounded_query_log_context(str(sample))
            logger.info(
                "Search budget rejected request (%s) preview=%r digest=%s",
                exc.kind,
                log_ctx["query_preview"],
                log_ctx["query_digest"],
            )
            resp.status = falcon.HTTP_400
            resp.media = {
                "title": "Invalid Search Query",
                "description": exc.user_message,
            }
            resp.complete = True
