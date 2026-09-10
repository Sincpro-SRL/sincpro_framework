"""Record an exception on a span. Never raises. Does not touch GlitchTip."""

from typing import Any

from sincpro_framework.observability.tracing.setup import OTEL_AVAILABLE


def span_error(span: Any, error: Exception) -> None:
    try:
        if OTEL_AVAILABLE and span is not None:
            from opentelemetry.trace import StatusCode

            span.record_exception(error)
            span.set_status(StatusCode.ERROR, str(error))
    except Exception:
        return
