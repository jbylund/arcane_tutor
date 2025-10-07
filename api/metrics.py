"""Queue-based metrics wrapper that mimics prometheus_client API.

This module provides Counter, Gauge, Histogram, and Summary classes that have
the same API as prometheus_client, but send metrics to a queue instead of
updating them directly. This allows metrics collection across multiple processes
without file I/O or lock contention.

Usage:
    from api.metrics import Counter, get_metrics_queue, set_metrics_queue

    # Set the queue (done in entrypoint.py)
    set_metrics_queue(my_queue)

    # Use like normal prometheus_client
    my_counter = Counter('my_metric_total', 'Description', ['label1'])
    my_counter.labels(label1='value').inc()
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import multiprocessing

logger = logging.getLogger(__name__)

# Global metrics queue
_metrics_queue: multiprocessing.Queue | None = None


def get_default_buckets() -> tuple[float, ...]:
    latency_median = 50 / 1000
    latency_buckets = {latency_median}
    ptr_low = ptr_high = latency_median
    for _ in range(7):
        ptr_high = ptr_high * 1.2
        ptr_low = ptr_low / 1.2
        latency_buckets.add(ptr_low)
        latency_buckets.add(ptr_high)
    return tuple(sorted(latency_buckets))

def set_metrics_queue(queue: multiprocessing.Queue) -> None:
    """Set the global metrics queue."""
    global _metrics_queue
    logger.info("Setting metrics queue to %s", queue)
    _metrics_queue = queue


def get_metrics_queue() -> multiprocessing.Queue | None:
    """Get the global metrics queue."""
    return _metrics_queue


class _LabeledMetric:
    """A labeled metric that sends updates to the queue."""

    def __init__(
        self,
        **kwargs,
    ) -> None:
        self.kwargs = kwargs

    def _send_message(self, *op_args, **op_kwargs) -> None:
        """Send a metric message to the queue."""
        queue = get_metrics_queue()
        if queue is None:
            logger.debug("Metrics queue not set, dropping metric: %s", self.name)
            return

        message = (self.kwargs, op_args, op_kwargs)
        logger.info("Sending metric message: %s", message)

        try:
            queue.put_nowait(message)
        except Exception as e:
            logger.warning("Failed to send metric to queue: %s", e)

    def inc(self, amount: float = 1.0) -> None:
        """Increment counter/gauge by amount."""
        self._send_message("inc", amount)

    def dec(self, amount: float = 1.0) -> None:
        """Decrement gauge by amount."""
        self._send_message("dec", amount)

    def set(self, value: float) -> None:
        """Set gauge to value."""
        self._send_message("set", value)

    def observe(self, value: float) -> None:
        """Observe a value for histogram/summary."""
        self._send_message("observe", value)


class _BaseMetric:
    """Base class for metrics."""

    def __init__(
        self,
        **kwargs: Any,
    ) -> None:
        self.kwargs = kwargs

    def labels(self, **labels: str) -> _LabeledMetric:
        """Return a labeled version of this metric."""
        # Validate that all required labels are provided
        provided_labels = set(labels)
        required_labels = set(self.kwargs.get("labelnames", ()))

        if provided_labels != required_labels:
            missing = required_labels - provided_labels
            extra = provided_labels - required_labels
            if missing:
                msg = f"Missing labels: {missing}"
                raise ValueError(msg)
            if extra:
                msg = f"Unexpected labels: {extra}"
                raise ValueError(msg)

        return _LabeledMetric(
            metric_type=self.__class__.__name__,
            labels=labels,
            **self.kwargs,
        )


class Counter(_BaseMetric):
    """Counter metric that sends increments to the queue.

    Counters can only increase (never decrease).

    Example:
        requests = Counter('http_requests_total', 'Total HTTP requests', ['method', 'endpoint'])
        requests.labels(method='GET', endpoint='/api').inc()
        requests.labels(method='POST', endpoint='/api').inc(5)
    """
    def inc(self, amount: float = 1.0) -> None:
        """Increment counter (for unlabeled metrics)."""
        labeled = _LabeledMetric(
            metric_type=self.__class__.__name__,
            **self.kwargs,
        )
        labeled.inc(amount)


class Gauge(_BaseMetric):
    """Gauge metric that can go up and down.

    Example:
        temperature = Gauge('room_temperature_celsius', 'Room temperature', ['room'])
        temperature.labels(room='kitchen').set(22.5)
        temperature.labels(room='kitchen').inc(0.5)
        temperature.labels(room='kitchen').dec(1.0)
    """

    def set(self, value: float) -> None:
        """Set gauge value (for unlabeled metrics)."""
        labeled = _LabeledMetric(self.metric_type, self.name, {})
        labeled.set(value)

    def inc(self, amount: float = 1.0) -> None:
        """Increment gauge (for unlabeled metrics)."""
        labeled = _LabeledMetric(self.metric_type, self.name, {})
        labeled.inc(amount)

    def dec(self, amount: float = 1.0) -> None:
        """Decrement gauge (for unlabeled metrics)."""
        labeled = _LabeledMetric(self.metric_type, self.name, {})
        labeled.dec(amount)


class Histogram(_BaseMetric):
    """Histogram metric that observes values and calculates distributions.

    Example:
        request_duration = Histogram('http_request_duration_seconds', 'Request duration', ['endpoint'])
        request_duration.labels(endpoint='/api').observe(0.042)
    """

    def __init__(
        self,
        *args,
        **kwargs,
    ) -> None:
        kwargs.setdefault("buckets", get_default_buckets())
        super().__init__(*args, **kwargs)

    def observe(self, value: float) -> None:
        """Observe a value (for unlabeled metrics)."""
        labeled = _LabeledMetric(self.metric_type, self.name, {})
        labeled.observe(value)


class Summary(_BaseMetric):
    """Summary metric that observes values and calculates quantiles.

    Example:
        response_size = Summary('http_response_size_bytes', 'Response size', ['endpoint'])
        response_size.labels(endpoint='/api').observe(1024)
    """

    def observe(self, value: float) -> None:
        """Observe a value (for unlabeled metrics)."""
        labeled = _LabeledMetric(self.metric_type, self.name, {})
        labeled.observe(value)
