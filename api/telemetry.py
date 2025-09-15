from __future__ import annotations

import os
from typing import Final

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter as OTLPHttpSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter as OTLPGrpcSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def setup_tracing(*, service_name: str = "apiservice") -> None:
    """Configure OpenTelemetry tracing with OTLP exporter.

    Chooses gRPC exporter if OTEL_EXPORTER_OTLP_TRACES_PROTOCOL=grpc, otherwise HTTP.
    Respects OTEL_EXPORTER_OTLP_ENDPOINT or Jaeger defaults.
    """
    protocol: str = os.getenv("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL", "grpc").lower()
    endpoint_env: str | None = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")

    if endpoint_env:
        endpoint = endpoint_env.rstrip("/")
    else:
        # Default to compose service name
        if protocol == "grpc":
            endpoint = "http://jaeger:4317"
        else:
            endpoint = "http://jaeger:4318"

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    trace.set_tracer_provider(provider)

    if protocol == "grpc":
        exporter = OTLPGrpcSpanExporter(endpoint=endpoint, insecure=True)
    else:
        # OTel HTTP exporter expects /v1/traces path
        if not endpoint.endswith("/v1/traces"):
            endpoint = f"{endpoint}/v1/traces"
        exporter = OTLPHttpSpanExporter(endpoint=endpoint)

    processor = BatchSpanProcessor(exporter)
    provider.add_span_processor(processor)
