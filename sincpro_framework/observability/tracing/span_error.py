"""Record an exception on a span. Never raises. Does not touch GlitchTip."""

from typing import Any

from sincpro_framework.observability.failure import CodeLocation
from sincpro_framework.observability.tracing.setup import OTEL_AVAILABLE


def span_error(span: Any, error: BaseException, where: CodeLocation | None) -> None:
    """Context: called once, on the span of the handler that raised. The status of every span
    the error crosses is set by the span itself as the error escapes it."""
    try:
        if not (OTEL_AVAILABLE and span is not None):
            return
        span.record_exception(error)
        span.set_attribute("error.type", type(error).__qualname__)
        if where is not None:
            span.set_attribute("code.function.name", f"{where.module}.{where.function}")
            span.set_attribute("code.file.path", where.file)
            span.set_attribute("code.line.number", where.line)
    except Exception:
        return
