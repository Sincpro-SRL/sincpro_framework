"""Observability for the framework: two doors, and what a use case says on its own span.

            from sincpro_framework.observability import traces

``Observability`` is what a ``UseFramework`` already owns: spans per DTO, errors to
GlitchTip, trace ids on its logs. ``process`` is for a service that also has to
instrument the transport around its buses — HTTP, httpx, the server's loggers —
without borrowing a bus's identity. ``traces`` is for a use case: what it puts on the
span the bus opened for it — a NIT, a merchant — declared with ``of()`` or by hand.

Everything else in this package is an implementation detail of those. The
framework never imports opentelemetry or sentry_sdk outside them, and works
unchanged when neither is installed.
"""

from sincpro_framework.observability.api import Observability, ProcessObservability, process
from sincpro_framework.observability.domain import (
    ComponentStatus,
    ObservabilityIdentity,
    ObservabilityStatus,
)
from sincpro_framework.observability.metrics.domain.paths import of
from sincpro_framework.observability.tracing.attributes import SpanValue, Traces, traces
from sincpro_framework.observability.tracing.propagation import (
    TRACE_HEADERS,
    trace_carrier,
    trace_id_of,
    within_trace,
)

__all__ = [
    "ComponentStatus",
    "Observability",
    "ObservabilityIdentity",
    "ObservabilityStatus",
    "ProcessObservability",
    "SpanValue",
    "TRACE_HEADERS",
    "Traces",
    "of",
    "process",
    "trace_carrier",
    "trace_id_of",
    "traces",
    "within_trace",
]
