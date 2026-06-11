"""Bus-level OTel span and log-correlation helpers."""

from contextlib import nullcontext
from typing import Any

try:
    import opentelemetry  # noqa: F401 — existence check only

    _OTEL_AVAILABLE = True
except ImportError:
    _OTEL_AVAILABLE = False


def _span(dto_name: str, layer: str, service_name: str = ""):
    """Return an OTel span CM or a no-op when OTel is not installed.

    Adds ``sincpro.layer`` and, when provided, ``sincpro.instance`` so spans
    from N independent UseFramework instances are distinguishable in the backend.
    The tracer name "sincpro_framework" identifies the instrumentation library;
    ``sincpro.instance`` carries the bounded-context name.
    """
    if _OTEL_AVAILABLE:
        from opentelemetry import trace as otel_trace

        attrs: dict = {"sincpro.layer": layer}
        if service_name:
            attrs["sincpro.instance"] = service_name
        return otel_trace.get_tracer("sincpro_framework").start_as_current_span(
            dto_name, attributes=attrs
        )
    return nullcontext()


def _record_span_error(span: Any, error: Exception) -> None:
    """Record an exception on the active span and mark its status as ERROR."""
    if _OTEL_AVAILABLE and span is not None:
        from opentelemetry.trace import StatusCode

        span.record_exception(error)
        span.set_status(StatusCode.ERROR, str(error))


def _log_ctx(bus_logger: Any, span: Any):
    """Bind the active OTel span's trace_id/span_id to the shared logger.

    Works for any case: explicit with_trace(), inherited outer span (FastAPI,
    Celery), or a fresh root span created by the bus itself.
    Returns a context manager that restores the logger's previous fields on exit.
    """
    if _OTEL_AVAILABLE and span is not None:
        span_ctx = span.get_span_context()
        if span_ctx.is_valid:
            return bus_logger.context(
                trace_id=format(span_ctx.trace_id, "032x"),
                span_id=format(span_ctx.span_id, "016x"),
            )
    return nullcontext()
