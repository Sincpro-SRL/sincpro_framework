"""Record an exception on a span. Never raises. Does not touch GlitchTip."""

from collections.abc import Mapping
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
        # `__name__`, as the metrics, the logs and GlitchTip spell it — one filter for all four.
        span.set_attribute("error.type", type(error).__name__)
        if where is not None:
            span.set_attribute("code.function.name", f"{where.module}.{where.function}")
            span.set_attribute("code.file.path", where.file)
            span.set_attribute("code.line.number", where.line)
    except Exception:
        return


def span_attributes(span: Any, attributes: Mapping[str, str]) -> None:
    if not (OTEL_AVAILABLE and span is not None):
        return
    for key, value in attributes.items():
        span.set_attribute(key, value)


def span_correlation(span: Any, keys: Mapping[str, Any]) -> None:
    """The execution's context on its span (PRD_03 §4.10): each value as OpenTelemetry takes
    it, and as its text when a span cannot hold it. Never raises."""
    if not (OTEL_AVAILABLE and span is not None):
        return
    from sincpro_framework.observability.tracing.attributes import span_value

    for key, value in keys.items():
        try:
            try:
                converted = span_value(value)
            except TypeError:
                converted = str(value)
            if converted is not None:
                span.set_attribute(key, converted)
        except Exception:
            continue
