"""Bus-level observability: span creation, log correlation, and error recording."""

from contextlib import contextmanager, nullcontext
from typing import Any, Generator

try:
    import opentelemetry  # noqa: F401 — existence check only

    _OTEL_AVAILABLE = True
except ImportError:
    _OTEL_AVAILABLE = False


@contextmanager
def observe_execution(
    dto_name: str, layer: str, instance: str, logger: Any
) -> Generator[Any, None, None]:
    """Observability context for a single DTO execution on a bus layer.

    Combines two concerns that must always travel together:
    - An OTel span named after the DTO, tagged with ``sincpro.layer`` and
      ``sincpro.instance`` so spans from different bounded contexts are
      distinguishable in the tracing backend.
    - Logger binding so every log line emitted inside this block carries the
      same ``trace_id`` / ``span_id`` as the exported OTel span.

    Yields the active span so callers can record errors via
    ``record_span_error(span, error)``.
    """
    with _dto_span(dto_name, layer, instance) as span:
        with _bind_span_to_logger(logger, span):
            yield span


def record_observability_span_error(span: Any, error: Exception) -> None:
    """Record an exception on the active span and mark its status as ERROR."""
    if _OTEL_AVAILABLE and span is not None:
        from opentelemetry.trace import StatusCode

        span.record_exception(error)
        span.set_status(StatusCode.ERROR, str(error))


# ---------------------------------------------------------------------------
# Private helpers — implementation details of this module only
# ---------------------------------------------------------------------------


def _dto_span(dto_name: str, layer: str, instance: str):
    """Return an OTel span CM tagged with sincpro attributes, or a no-op."""
    if _OTEL_AVAILABLE:
        from opentelemetry import trace as otel_trace

        attrs: dict = {"sincpro.layer": layer}
        if instance:
            attrs["sincpro.instance"] = instance
        return otel_trace.get_tracer("sincpro_framework").start_as_current_span(
            dto_name, attributes=attrs
        )
    return nullcontext()


def _bind_span_to_logger(logger: Any, span: Any):
    """Bind the active OTel span's trace_id/span_id to the shared logger.

    Works for any case: explicit with_trace(), inherited outer span (FastAPI,
    Celery), or a fresh root span created by the bus itself.
    Returns a context manager that restores the logger's previous fields on exit.
    """
    if _OTEL_AVAILABLE and span is not None:
        span_ctx = span.get_span_context()
        if span_ctx.is_valid:
            return logger.context(
                trace_id=format(span_ctx.trace_id, "032x"),
                span_id=format(span_ctx.span_id, "016x"),
            )
    return nullcontext()
