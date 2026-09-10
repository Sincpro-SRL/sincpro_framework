"""Observability for the framework: one object, one import.

    from sincpro_framework.observability import Observability

Everything else in this package is an implementation detail of that object. The
framework never imports opentelemetry or sentry_sdk directly, and works unchanged
when neither is installed.
"""

from sincpro_framework.observability.api import Observability
from sincpro_framework.observability.domain import (
    ComponentStatus,
    ObservabilityIdentity,
    ObservabilityStatus,
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
    "framework_identity",
    "framework_version",
    "registry",
    "resolve_identity",
]
