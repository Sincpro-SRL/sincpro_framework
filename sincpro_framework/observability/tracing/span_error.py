"""Record an exception on a span. Never raises. Does not touch GlitchTip."""

from collections.abc import Mapping
from typing import Any

from sincpro_framework.observability.failure import CodeLocation
from sincpro_framework.observability.tracing.attributes import span_value
from sincpro_framework.observability.tracing.setup import OTEL_AVAILABLE


def span_error(span: Any, error: BaseException, where: CodeLocation | None) -> None:
    """Put `error` on `span` — the exception event and where in the code it failed.

    Context: called once, on the span of the handler that raised. The status of every span
    the error crosses is set by the span itself as the error escapes it.

        span_error(span, KeyError("x"),
                   CodeLocation("billing.features", "CreateInvoice.execute", "/app/f.py", 42))
            ->  an exception event; error.type="KeyError",
                code.function.name="billing.features.CreateInvoice.execute",
                code.file.path="/app/f.py", code.line.number=42
        span_error(None, error, where)   ->  nothing

    With `where` unknown only the event and `error.type` are set; the status is untouched."""
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


def span_in_error(span: Any, error: BaseException) -> None:
    """The span's status ERROR — set on every span a fault crosses; a failure that is not one
    (something not found, refused, invalid) leaves it unset, as OpenTelemetry asks of a server
    span answering a client's mistake. Never raises.

        span_in_error(span, KeyError("x"))   ->  span status Status(ERROR, "KeyError")
        span_in_error(None, KeyError("x"))   ->  nothing

    It does not judge: the caller decides the error is a fault (`Observability.faulted`)."""
    try:
        if not (OTEL_AVAILABLE and span is not None):
            return
        from opentelemetry.trace import Status, StatusCode

        span.set_status(Status(StatusCode.ERROR, type(error).__name__))
    except Exception:
        return


def span_attributes(span: Any, attributes: Mapping[str, str]) -> None:
    """Set `attributes` on `span` as they are — text already; nothing without a span or
    without opentelemetry."""
    if not (OTEL_AVAILABLE and span is not None):
        return
    for key, value in attributes.items():
        span.set_attribute(key, value)


def span_correlation(span: Any, keys: Mapping[str, Any]) -> None:
    """The execution's context on its span (PRD_03 §4.10): each value as OpenTelemetry takes
    it, and as its text when a span cannot hold it. Never raises.

        span_correlation(span, {"tenant": "acme", "company": 3, "region": Region.EU,
                                "filters": {"a": 1}, "note": None})
            ->  tenant="acme", company=3, region="eu", filters="{'a': 1}"; no note

    1. No span, or no OpenTelemetry: nothing.
    2. For each key and value:
       2.1 converted as `span_value` does — an Enum's value, a Decimal as a float, …;
       2.2 a value a span cannot hold (`TypeError`) travels as its `str`;
       2.3 `None` is left off; anything else is set under its key.
    Final: a key whose conversion or setting raises otherwise is skipped, the rest still set.
    """
    if not (OTEL_AVAILABLE and span is not None):
        return
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
