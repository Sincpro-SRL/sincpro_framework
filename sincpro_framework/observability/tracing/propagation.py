"""A trace carried across a process boundary — a broker, a remote call, a queue — and adopted on
the other side: one trace instead of two unrelated ones. W3C Trace Context (`traceparent`,
`tracestate`); the headers and their reading live here and nowhere else.

Both are no-ops without OpenTelemetry, so the core needs nothing.
"""

from collections.abc import Generator, Mapping
from contextlib import contextmanager

TRACE_HEADERS = ("traceparent", "tracestate")
"""The W3C headers a trace travels in."""


def trace_id_of(traceparent: str | None) -> str | None:
    """The trace id a `traceparent` names, or ``None`` when it names none.

    in  "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"  →  out  "4bf92f…4736"
    in  "garbage"                                                 →  out  None
    """
    parts = (traceparent or "").split("-")
    return parts[1] if len(parts) >= 3 and len(parts[1]) == 32 else None


def trace_carrier() -> dict[str, str]:
    """The trace that is running right now, as W3C headers — `{'traceparent': '00-…'}`, and
    empty when nothing is tracing or OpenTelemetry is not installed.

    The event itself is left alone: a trace is about the call that produced the fact, not part
    of the fact, so it rides beside the payload rather than inside `DomainEvent`.
    """
    try:
        from opentelemetry.propagate import inject
    except ImportError:
        return {}
    carrier: dict[str, str] = {}
    inject(carrier)
    return carrier


@contextmanager
def within_trace(carrier: Mapping[str, str]) -> Generator[None]:
    """Runs the block inside the trace the carrier names, so what the consumer does lands
    under the span that published — one trace across the process boundary instead of two
    unrelated ones. Without a carrier, or without OpenTelemetry, it is a plain block.

    The same adoption `entrypoints/rpc/entrypoint.py` does for an incoming `traceparent`
    header; this is that boundary, asynchronous.
    """
    if not carrier:
        yield
        return
    try:
        from opentelemetry import context
        from opentelemetry.propagate import extract
    except ImportError:
        yield
        return
    token = context.attach(extract(carrier))
    try:
        yield
    finally:
        context.detach(token)
