"""Open a DTO span and bind its ids and coordinates to the logger. Never raises. Not
GlitchTip's job."""

from collections.abc import Mapping
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from typing import Any, Generator

from sincpro_framework.observability.tracing.setup import OTEL_AVAILABLE, tracer_for

_use_case_span: ContextVar[Any] = ContextVar("sincpro_use_case_span", default=None)
"""The span of the use case running now — `None` outside one, or with no tracer. Kept apart from
OTel's active span: an adapter that opens a span of its own (an HTTP client, a SQL statement) must
not become where the use case's attributes land."""


def use_case_span() -> Any:
    return _use_case_span.get()


@contextmanager
def _running(span: Any) -> Generator[None, None, None]:
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


def _span_for(dto_name: str, layer: str, bus: str, attributes: Mapping[str, str]) -> Any:
    """Context: named ``context/DTO`` — the service is the artifact, so the bounded context
    shows in the name as an RPC span shows ``service/method``; ``sincpro.context`` and
    ``sincpro.use_case`` carry the two halves for filtering."""
    if not OTEL_AVAILABLE:
        return nullcontext()
    try:
        tracer = tracer_for(bus)
        if tracer is None:
            return nullcontext()
        on_span = {"sincpro.layer": layer, "sincpro.use_case": dto_name, **attributes}
        if bus:
            on_span["sincpro.instance"] = bus
            on_span["sincpro.context"] = bus
        # The exception is recorded once, by span_error, on the handler that raised it; the
        # span only takes the error status as the exception crosses it.
        return tracer.start_as_current_span(
            f"{bus}/{dto_name}" if bus else dto_name,
            attributes=on_span,
            record_exception=False,
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
) -> Generator[Any, None, None]:
    coordinates = {"sincpro_use_case": dto_name, "sincpro_layer": layer}
    if bus:
        coordinates["sincpro_context"] = bus
    with _shielded(_span_for(dto_name, layer, bus, attributes or {})) as span, _running(span):
        with _shielded(_log_fields_of(span, logger, coordinates)):
            yield span
