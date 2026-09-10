"""Observability for the framework: two doors, nothing else.

    from sincpro_framework.observability import Observability   # one per bus
    from sincpro_framework.observability import process         # the transport

``Observability`` is what a ``UseFramework`` already owns: spans per DTO, errors to
GlitchTip, trace ids on its logs. ``process`` is for a service that also has to
instrument the transport around its buses — HTTP, httpx, the server's loggers —
without borrowing a bus's identity.

Everything else in this package is an implementation detail of those two. The
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
from sincpro_framework.observability.registry import registry
from sincpro_framework.observability.tracing.span_context import FrameworkSpanContext

__all__ = [
    "ComponentStatus",
    "FrameworkSpanContext",
    "Observability",
    "ObservabilityIdentity",
    "ObservabilityStatus",
    "ProcessObservability",
    "caller_module",
    "framework_identity",
    "framework_version",
    "process",
    "registry",
    "resolve_identity",
]
