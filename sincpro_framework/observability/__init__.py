"""Observability for the framework: two doors, and what a use case says on its own span.

    from sincpro_framework.observability import Observability   # one per bus
    from sincpro_framework.observability import process         # the transport
    from sincpro_framework.observability import traces, of      # a use case's span attributes

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
    caller_module,
    framework_identity,
    framework_version,
    resolve_identity,
)
from sincpro_framework.observability.metrics.domain.paths import of
from sincpro_framework.observability.registry import registry
from sincpro_framework.observability.tracing.attributes import SpanValue, Traces, traces
from sincpro_framework.observability.tracing.span_context import FrameworkSpanContext

__all__ = [
    "ComponentStatus",
    "FrameworkSpanContext",
    "Observability",
    "ObservabilityIdentity",
    "ObservabilityStatus",
    "ProcessObservability",
    "SpanValue",
    "Traces",
    "caller_module",
    "framework_identity",
    "framework_version",
    "of",
    "process",
    "registry",
    "resolve_identity",
    "traces",
]
