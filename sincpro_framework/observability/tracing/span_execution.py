"""Open a DTO span and bind its ids to the logger. Never raises. Not GlitchTip's job."""

from contextlib import contextmanager, nullcontext
from typing import Any, Generator

from sincpro_framework.observability.tracing.setup import OTEL_AVAILABLE, tracer_for


def _span_for(dto_name: str, layer: str, bus: str) -> Any:
    if not OTEL_AVAILABLE:
        return nullcontext()
    try:
        tracer = tracer_for(bus)
        if tracer is None:
            return nullcontext()
        attributes = {"sincpro.layer": layer}
        if bus:
            attributes["sincpro.instance"] = bus
        return tracer.start_as_current_span(dto_name, attributes=attributes)
    except Exception:
        return nullcontext()


def _log_ids_of(span: Any, logger: Any) -> Any:
    if not OTEL_AVAILABLE or span is None:
        return nullcontext()
    try:
        span_ctx = span.get_span_context()
        if not span_ctx.is_valid:
            return nullcontext()
        return logger.context(
            trace_id=format(span_ctx.trace_id, "032x"),
            span_id=format(span_ctx.span_id, "016x"),
        )
    except Exception:
        return nullcontext()


@contextmanager
def span_execution(
    dto_name: str, layer: str, bus: str, logger: Any
) -> Generator[Any, None, None]:
    with _span_for(dto_name, layer, bus) as span:
        with _log_ids_of(span, logger):
            yield span
