from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from opentelemetry import context, trace
from opentelemetry.trace import Span, Tracer

if TYPE_CHECKING:
    import falcon


class TracingMiddleware:
    """Middleware that creates a request span and optionally spans for function calls.

    Function-call spans are disabled by default for performance. Enable with
    environment variable `OTEL_ENABLE_FUNCTION_SPANS=true`.
    """

    def __init__(self: TracingMiddleware) -> None:
        self._tracer: Tracer = trace.get_tracer("scryfallos.api")
        self._enable_function_spans: bool = os.getenv("OTEL_ENABLE_FUNCTION_SPANS", "false").lower() in {"1", "true", "yes"}
        self._max_function_spans_per_request: int = int(os.getenv("OTEL_SPAN_LIMIT_PER_REQUEST", "500"))
        default_prefix = str(Path(__file__).resolve().parents[1])  # /.../scryfallos/api
        raw_prefixes = os.getenv("OTEL_FUNCTION_SPAN_MODULE_PREFIXES", default_prefix)
        self._allowed_prefixes: tuple[str, ...] = tuple(p.strip() for p in raw_prefixes.split(",") if p.strip())

    def process_request(self: TracingMiddleware, req: falcon.Request, resp: falcon.Response) -> None:
        span_name = f"{req.method} {req.path}"
        span = self._tracer.start_span(span_name)
        span.set_attribute("http.request.method", req.method)
        span.set_attribute("url.path", req.path)
        if req.query_string:
            span.set_attribute("url.query", req.query_string)
        user_agent = req.get_header("User-Agent")
        if user_agent:
            span.set_attribute("user_agent.original", user_agent)

        token = context.attach(trace.set_span_in_context(span))
        req.context["_otel_token"] = token
        req.context["_otel_span"] = span

        if self._enable_function_spans:
            self._install_function_profiler(req=req)

    def process_response(
        self: TracingMiddleware,
        req: falcon.Request,
        resp: falcon.Response,
        resource: object,
        req_succeeded: bool,
    ) -> None:
        if self._enable_function_spans:
            self._uninstall_function_profiler(req=req)

        token = req.context.pop("_otel_token", None)
        span: Span | None = req.context.pop("_otel_span", None)
        if token is not None:
            context.detach(token)
        if span is not None:
            span.set_attribute("http.response.status_code", getattr(resp, "status", None))
            span.end()

    # --- Function-call span profiler -------------------------------------------------

    def _install_function_profiler(self: TracingMiddleware, *, req: falcon.Request) -> None:
        tracer = self._tracer
        allowed_prefixes = self._allowed_prefixes
        max_spans = self._max_function_spans_per_request
        spans_by_frame_id: dict[int, tuple[Span, object]] = {}
        span_count = 0

        def should_instrument(file_path: str) -> bool:
            for prefix in allowed_prefixes:
                if file_path.startswith(prefix):
                    return True
            return False

        def profile_func(frame, event: str, arg):  # type: ignore[no-untyped-def]
            nonlocal span_count
            if event not in ("call", "return"):
                return
            file_path = frame.f_code.co_filename
            if not should_instrument(file_path):
                return

            frame_id = id(frame)
            if event == "call":
                if span_count >= max_spans:
                    return
                func_name = frame.f_code.co_name
                name = f"{Path(file_path).name}:{func_name}"
                span = tracer.start_span(name)
                token = context.attach(trace.set_span_in_context(span))
                spans_by_frame_id[frame_id] = (span, token)
                span_count += 1
            elif event == "return":
                entry = spans_by_frame_id.pop(frame_id, None)
                if entry is None:
                    return
                span, token = entry
                try:
                    context.detach(token)
                finally:
                    span.end()

        req.context["_otel_profiler"] = profile_func  # type: ignore[assignment]
        req.context["_otel_profiler_spans"] = spans_by_frame_id
        sys.setprofile(profile_func)  # Applies to current thread

    def _uninstall_function_profiler(self: TracingMiddleware, *, req: falcon.Request) -> None:
        profiler: Callable | None = req.context.pop("_otel_profiler", None)
        spans_by_frame_id: dict[int, tuple[Span, object]] | None = req.context.pop("_otel_profiler_spans", None)
        if profiler is not None:
            sys.setprofile(None)
        if spans_by_frame_id:
            # End any spans left open defensively
            for _, (span, token) in list(spans_by_frame_id.items()):
                try:
                    context.detach(token)
                finally:
                    span.end()
