"""Metrics aggregator using queue-based approach for multiprocessing."""

from __future__ import annotations

import logging
import multiprocessing
import threading
from typing import Any

import falcon
import prometheus_client
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, Summary, generate_latest

logger = logging.getLogger(__name__)

# Registry of metrics by name - maps metric names to actual prometheus_client objects
_metrics_registry: dict[str, Counter | Gauge | Histogram | Summary] = {}


class MetricsEndpoint:
    """Falcon endpoint for serving Prometheus metrics."""

    def on_get(self, _req: falcon.Request, resp: falcon.Response) -> None:
        """Serve metrics in Prometheus format."""
        resp.content_type = CONTENT_TYPE_LATEST
        resp.data = generate_latest()


class MetricsAggregator(multiprocessing.Process):
    """Process that aggregates metrics from workers via a queue."""

    def __init__(self, port: int = 8081, metrics_queue: multiprocessing.Queue | None = None) -> None:
        """Initialize metrics aggregator process.

        Args:
            port: Port to serve metrics on
            metrics_queue: Queue to receive metrics from workers
        """
        super().__init__()
        self.port = port
        self.metrics_queue = metrics_queue
        self.app = falcon.App()
        metrics_endpoint = MetricsEndpoint()
        self.app.add_route("/", metrics_endpoint)
        self.app.add_route("/metrics", metrics_endpoint)
        self._stop_event = threading.Event()

    def _consume_metrics(self) -> None:
        """Consume metrics messages from the queue and update Prometheus metrics."""
        logger.info("Starting metrics consumer thread")

        while not self._stop_event.is_set():
            try:
                # Non-blocking get with timeout to allow clean shutdown
                message = self.metrics_queue.get(timeout=0.1)
                self._process_metric_message(message)
            except multiprocessing.queues.Empty:
                continue
            except Exception as e:
                logger.error("Error processing metric: %s", e, exc_info=True)

        # Drain remaining messages
        try:
            while True:
                message = self.metrics_queue.get_nowait()
                self._process_metric_message(message)
        except multiprocessing.queues.Empty:
            pass

        logger.info("Metrics consumer thread stopped")


    def _process_metric_message(self, message: dict[str, Any]) -> None:
        """Process a single metric message."""
        logger.debug("Processing metric message: %s", message)
        metric_kwargs, (op_name, *op_args), op_kwargs = message
        metric_name = metric_kwargs.pop("name")
        labels = metric_kwargs.pop("labels", {})
        metric = _metrics_registry.get(metric_name)
        if metric is None:
            metric_cls = getattr(prometheus_client, metric_kwargs.pop("metric_type"))
            # ... need to construct the thing
            _metrics_registry[metric_name] = metric = metric_cls(metric_name, **metric_kwargs)

        # Apply labels if present
        labeled_metric = metric.labels(**labels) if labels else metric

        op = getattr(labeled_metric, op_name)
        op(*op_args, **op_kwargs)


    def run(self) -> None:
        """Run the metrics server and consumer."""
        import waitress  # noqa: PLC0415

        logger.info("Starting metrics aggregator on port %d", self.port)
        logger.info("Metrics will be created dynamically as they are received")

        if self.metrics_queue is None:
            logger.warning("No metrics queue provided, metrics will not be collected")
        else:
            # Start consumer thread
            consumer_thread = threading.Thread(target=self._consume_metrics, daemon=True)
            consumer_thread.start()

        # Start web server (this blocks)
        try:
            waitress.serve(self.app, host="0.0.0.0", port=self.port)  # noqa: S104
        finally:
            self._stop_event.set()
            if self.metrics_queue:
                consumer_thread.join(timeout=5)
