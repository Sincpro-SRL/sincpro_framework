"""The framework's single door to observability.

One ``Observability`` per ``UseFramework``. It knows who the bus is, brings the
backends up, and is the only object ``bus.py`` and ``use_bus.py`` talk to — so the
rest of the framework never imports opentelemetry, sentry_sdk, or anything under
``observability.errors`` / ``observability.tracing``.

Nothing here raises and nothing here is required: with neither extra installed every
method is a no-op and the bus runs exactly as it would without observability.
"""

from typing import Any, ContextManager, Optional, Tuple, Type

from sincpro_log.logger import LoggerProxy

from sincpro_framework.observability.domain import (
    ObservabilityIdentity,
    ObservabilityStatus,
    resolve_identity,
)
from sincpro_framework.observability.errors.record_error import ErrorKind, record_error
from sincpro_framework.observability.errors.setup import setup as setup_errors
from sincpro_framework.observability.tracing.setup import setup as setup_tracing
from sincpro_framework.observability.tracing.span_context import FrameworkSpanContext
from sincpro_framework.observability.tracing.span_error import span_error
from sincpro_framework.observability.tracing.span_execution import span_execution

IgnoredExceptions = Tuple[Type[Exception], ...]


class Observability:
    """Who this bus is, which backends came up, and how it reports what happens."""

    def __init__(self, bus: str = "", package: str = "", version: str = "") -> None:
        self._bus = bus
        self._package = package
        self._version = version
        self._identity: Optional[ObservabilityIdentity] = None
        self.status: ObservabilityStatus = ObservabilityStatus()
        self.ignored_errors: IgnoredExceptions = ()
        self.logger: Optional[LoggerProxy] = None

    @property
    def identity(self) -> ObservabilityIdentity:
        """Resolved once, on first use — the distribution lookup behind it is not free."""
        if self._identity is None:
            self._identity = resolve_identity(self._bus, self._package, self._version)
        return self._identity

    def start(self, logger: Optional[LoggerProxy] = None) -> ObservabilityStatus:
        """Bring both backends up for this bus. Each one degrades on its own."""
        self.logger = logger
        self.status = ObservabilityStatus(
            sentry=setup_errors(self.identity),
            otel=setup_tracing(self.identity, logger),
        )
        message = (
            f"observability sentry={self.status.sentry.state}:{self.status.sentry.reason} "
            f"otel={self.status.otel.state}:{self.status.otel.reason}"
        )
        try:
            if "failed" in (self.status.sentry.state, self.status.otel.state):
                logger.warning(message)  # type: ignore[union-attr]
            else:
                logger.info(message)  # type: ignore[union-attr]
        except Exception:
            pass
        return self.status

    def ignore(self, *error_types: Type[Exception]) -> None:
        """Keep these exception types out of GlitchTip. They are expected, not bugs."""
        known = list(self.ignored_errors)
        for error_type in error_types:
            if error_type not in known:
                known.append(error_type)
        self.ignored_errors = tuple(known)

    def span(self, dto_name: str, layer: str) -> ContextManager[Any]:
        """Span for one DTO execution, with its ids bound to the logger."""
        return span_execution(dto_name, layer, self.identity.bus, self.logger)

    def record_error(
        self,
        error: Exception,
        dto_name: str,
        layer: str,
        span: Any = None,
        kind: ErrorKind = "instance",
    ) -> None:
        """Report a failure everywhere it belongs: on the span and in GlitchTip."""
        span_error(span, error)
        record_error(
            error,
            dto_name,
            layer,
            self.identity,
            kind=kind,
            ignored_exceptions=self.ignored_errors,
        )

    def trace_context(
        self,
        framework: Any,
        trace_id: Optional[str] = None,
        span_id: Optional[str] = None,
        carrier: Optional[Any] = None,
        adopt_active: bool = False,
    ) -> FrameworkSpanContext:
        """The ``with_trace()`` / ``with_parent_trace()`` block for this bus."""
        return FrameworkSpanContext(
            framework,
            self.identity,
            trace_id=trace_id,
            span_id=span_id,
            carrier=carrier,
            adopt_active=adopt_active,
        )
