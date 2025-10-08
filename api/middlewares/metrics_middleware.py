"""Middleware for collecting and reporting metrics via queue."""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING, Any

from .. import metrics

if TYPE_CHECKING:
    import multiprocessing

logger = logging.getLogger(__name__)


# Define metrics using the queue-based wrapper
request_count = metrics.Counter(
    name="api_requests_total",
    documentation="Total number of API requests",
    labelnames=["method", "endpoint", "status_code"],
)

latency_median = 50 / 1000
latency_buckets = {latency_median}
ptr_low = ptr_high = latency_median
for _ in range(7):
    ptr_high = ptr_high * 1.2
    ptr_low = ptr_low / 1.2
    latency_buckets.add(ptr_low)
    latency_buckets.add(ptr_high)
latency_buckets = tuple(sorted(round(x, 4) for x in latency_buckets))



request_duration = metrics.Histogram(
    name="api_request_duration_seconds",
    documentation="Request duration in seconds",
    labelnames=["method", "endpoint"],
    buckets=latency_buckets,
)

worker_requests = metrics.Counter(
    name="api_worker_requests_total",
    documentation="Total requests per worker",
    labelnames=["worker_id"],
)


class MetricsMiddleware:
    """Middleware that collects metrics using queue-based prometheus_client wrapper."""

    def __init__(self, metrics_queue: multiprocessing.Queue) -> None:
        """Initialize metrics middleware.

        Args:
            metrics_queue: Queue to send metrics to aggregator process
        """
        self.worker_id = os.getpid()
        logger.info("MetricsMiddleware initialized with worker id %d", self.worker_id)
        # Set the global queue for the metrics module
        metrics.set_metrics_queue(metrics_queue)

    def process_request(self, req: Any, _resp: Any) -> None:  # noqa: ANN401
        """Process incoming request."""
        req.start_time = time.monotonic()

    def process_response(
        self,
        req: Any,  # noqa: ANN401
        resp: Any,  # noqa: ANN401
        _resource: Any,  # noqa: ANN401
        _req_succeeded: bool,
    ) -> None:
        """Process response and record metrics."""
        if not hasattr(req, "start_time"):
            logger.warning("Request has no start time: %s", req.uri)
            return
        duration = time.monotonic() - req.start_time

        try:
            self._record_metrics(req, resp, duration)
        except (OSError, ValueError) as e:
            logger.debug("Failed to record metrics: %s", e)


    def _record_metrics(self, req: Any, resp: Any, duration: float) -> None:  # noqa: ANN401
        """Record metrics using the queue-based wrapper."""
        try:
            endpoint = req.path or "/"
            method = req.method or "GET"
            status = str(resp.status).split()[0] if resp.status else "200"

            # Use familiar prometheus_client API - but it sends to queue behind the scenes
            request_count.labels(method=method, endpoint=endpoint, status_code=status).inc()
            request_duration.labels(method=method, endpoint=endpoint).observe(duration)
            worker_requests.labels(worker_id=self.worker_id).inc()

            logger.debug(
                "METRICS: worker=%d method=%s endpoint=%s status=%s duration=%.3f",
                self.worker_id,
                method,
                endpoint,
                status,
                duration,
            )
        except Exception as oops:
            logger.error("Failed to record metrics: %s", oops, exc_info=True)
