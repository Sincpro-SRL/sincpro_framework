"""Open a DTO span and bind its ids and coordinates to the logger. Never raises. Not
GlitchTip's job.

    with span_execution("CreateInvoice", "feature", "billing", logger) as span:
        use_case_span()   ->  span        # where `traces.annotate` writes
        ...               # every log line carries sincpro_context, sincpro_use_case, trace_id
"""

from collections.abc import Mapping
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from typing import Any, Generator

from sincpro_framework.observability.tracing.setup import OTEL_AVAILABLE, tracer_for

_use_case_span: ContextVar[Any] = ContextVar("sincpro_use_case_span", default=None)
"""The span of the use case running now — `None` outside one, or with no tracer. Separate from
OTel's active span: an adapter that opens a span of its own (an HTTP client, a SQL statement) must
not become where the use case's attributes land."""


def use_case_span() -> Any:
    """The span of the use case running now — `None` outside one or without a tracer."""
    return _use_case_span.get()


@contextmanager
def _running(span: Any) -> Generator[None, None, None]:
    """Make `span` the use case's span for the block, and put back the one before after it."""
    token = _use_case_span.set(span)
    try:
        yield
    finally:
        try:
            _use_case_span.reset(token)
        except ValueError:
            # Closed from another context than the one it was opened in: nothing of ours is
            # set in this one.
            pass


@contextmanager
def _shielded(cm: Any) -> Generator[Any, None, None]:
    """Run a block inside ``cm`` without letting ``cm`` break the block.

    Entering, exiting and closing are all observability work: a span processor
    that raises on ``on_end``, a logger that fails to unbind. None of that may
    reach the bus. The block's **own** exception always propagates — a failing
    exporter must never swallow a business error, so a suppressing ``__exit__``
    is ignored on purpose.
    """
    try:
        value = cm.__enter__()
    except Exception:
        yield None
        return

    try:
        yield value
    except BaseException as error:
        try:
            cm.__exit__(type(error), error, error.__traceback__)
        except Exception:
            pass
        raise
    else:
        try:
            cm.__exit__(None, None, None)
        except Exception:
            pass


def _span_for(
    dto_name: str, layer: str, bus: str, attributes: Mapping[str, str], use_case: str
) -> Any:
    """The span of one use case run — a no-op context when OpenTelemetry is not there.

    Context: named ``context/DTO`` — the service is the artifact, so the bounded context
    shows in the name as an RPC span shows ``service/method``; ``sincpro.context`` carries the
    context and ``sincpro.use_case`` the use case's identity (``context.Class``) for
    filtering."""
    if not OTEL_AVAILABLE:
        return nullcontext()
    try:
        tracer = tracer_for(bus)
        if tracer is None:
            return nullcontext()
        on_span = {"sincpro.layer": layer, "sincpro.use_case": use_case, **attributes}
        if bus:
            on_span["sincpro.instance"] = bus
            on_span["sincpro.context"] = bus
        # The exception is recorded once, by span_error, on the handler that raised it; the
        # error status is the bus's to set (`span_in_error`), and only on a fault.
        return tracer.start_as_current_span(
            f"{bus}/{dto_name}" if bus else dto_name,
            attributes=on_span,
            record_exception=False,
            set_status_on_exception=False,
        )
    except Exception:
        return nullcontext()


def _log_fields_of(span: Any, logger: Any, coordinates: Mapping[str, str]) -> Any:
    """The execution's coordinates on every line inside it, with or without a tracer; the
    span's ids beside them when there is one."""
    try:
        fields = dict(coordinates)
        if OTEL_AVAILABLE and span is not None:
            span_ctx = span.get_span_context()
            if span_ctx.is_valid:
                fields["trace_id"] = format(span_ctx.trace_id, "032x")
                fields["span_id"] = format(span_ctx.span_id, "016x")
        return logger.context(**fields)
    except Exception:
        return nullcontext()


@contextmanager
def span_execution(
    dto_name: str,
    layer: str,
    bus: str,
    logger: Any,
    attributes: Mapping[str, str] | None = None,
    use_case: str = "",
) -> Generator[Any, None, None]:
    """`use_case` is the identity the span and the log lines carry — `dto_name` when none is
    given (a cron names itself).

        with span_execution("CreateInvoice", "feature", "billing", logger,
                            {"sincpro.outcome": "ok"}, "billing.CreateInvoice") as span:
            ...   ->  span "billing/CreateInvoice": sincpro.layer="feature",
                      sincpro.use_case="billing.CreateInvoice", sincpro.context="billing", …
                      every line: sincpro_use_case, sincpro_layer, sincpro_context,
                      trace_id, span_id
        with span_execution("nightly_close", "cron", "", logger) as span:
            ...   ->  span "nightly_close"; the lines carry no sincpro_context

    The span is `None` without a tracer — the lines still carry the coordinates; the span
    and the logger binding are shielded, so neither can break the block or hide its error."""
    use_case = use_case or dto_name
    coordinates = {"sincpro_use_case": use_case, "sincpro_layer": layer}
    if bus:
        coordinates["sincpro_context"] = bus
    opened = _span_for(dto_name, layer, bus, attributes or {}, use_case)
    with _shielded(opened) as span, _running(span):
        with _shielded(_log_fields_of(span, logger, coordinates)):
            yield span
