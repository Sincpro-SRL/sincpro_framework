"""The trace that published an event, carried beside it and adopted where it is consumed — one
trace across a process or a broker instead of two unrelated ones.

Both are no-ops without OpenTelemetry, so the core needs nothing.
"""

from collections.abc import Generator, Mapping
from contextlib import contextmanager


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
        from opentelemetry import context as otel_context
        from opentelemetry.propagate import extract
    except ImportError:
        yield
        return
    token = otel_context.attach(extract(carrier))
    try:
        yield
    finally:
        otel_context.detach(token)
