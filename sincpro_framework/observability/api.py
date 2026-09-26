"""The two doors to observability.

``Observability`` — one per ``UseFramework``. It knows who the bus is, brings the
backends up, and is the only object ``bus.py`` and ``use_bus.py`` talk to, so the
rest of the framework never imports opentelemetry, sentry_sdk, or anything under
``observability.errors`` / ``observability.tracing``.

``process`` — the transport around those buses. A bus is instrumented for you; the
ASGI request, the httpx call, the server's own loggers and a 500 that dies before any
Feature runs belong to the **process**, and must not be exported under a bus's name.

Nothing here raises and nothing here is required: with neither extra installed every
method is a no-op and the bus runs exactly as it would without observability.
"""

from typing import Any, ContextManager, Optional, Tuple, Type

from sincpro_log.logger import LoggerProxy, create_logger

from sincpro_framework.observability import failure
from sincpro_framework.observability.domain import (
    ComponentStatus,
    ObservabilityIdentity,
    ObservabilityStatus,
    caller_module,
    off,
    on,
    resolve_identity,
)
from sincpro_framework.observability.errors.record_error import ErrorKind, record_error
from sincpro_framework.observability.errors.setup import setup as setup_errors
from sincpro_framework.observability.registry import PROCESS, registry
from sincpro_framework.observability.tracing.setup import (
    OTEL_AVAILABLE,
    current_otel_context,
    host_provider_is_real,
)
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
        # Read here, not on first use: at this point the stack still holds the
        # library that is building the bus. From a request it would name the host.
        self._module_name = caller_module()
        self._identity: Optional[ObservabilityIdentity] = None
        self.status: ObservabilityStatus = ObservabilityStatus()
        self.ignored_errors: IgnoredExceptions = ()
        self.logger: LoggerProxy = create_logger(bus or "sincpro_framework")

    @property
    def identity(self) -> ObservabilityIdentity:
        """Resolved once, on first use — the distribution lookup behind it is not free."""
        if self._identity is None:
            self._identity = resolve_identity(
                self._bus, self._package, self._version, self._module_name
            )
        return self._identity

    def start(self, logger: Optional[LoggerProxy] = None) -> ObservabilityStatus:
        """Bring both backends up for this bus. Each one degrades on its own."""
        if logger is not None:
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
        span_error(span, error, None)
        record_error(
            error,
            dto_name,
            layer,
            self.identity,
            kind=kind,
            ignored_exceptions=self.ignored_errors,
        )

    # -----------------------------------------------------------------------------------------
    # One failure, reported once
    # -----------------------------------------------------------------------------------------

    def _is_expected(self, error: BaseException) -> bool:
        return isinstance(error, self.ignored_errors)

    def _describe(self, error: BaseException, dto: object) -> tuple[str, dict[str, Any]]:
        recorded = failure.failure_of(error)
        if recorded is None:
            fields = {"dto": repr(dto), "error_type": type(error).__name__}
            return f"{type(dto).__name__} failed: {type(error).__name__}: {error}", fields
        fields = recorded.fields()
        if error is not recorded.error:
            fields["raised_as"] = type(error).__name__
        return recorded.summary, fields

    def execution(self) -> ContextManager[bool]:
        return failure.execution()

    def handling(self, dto_name: str) -> ContextManager[None]:
        return failure.handling(dto_name)

    def failed(
        self, error: Exception, dto: object, handler: object, layer: str, span: Any
    ) -> None:
        """Context: the handler that raised describes the failure, puts it on its span and
        sends it to GlitchTip. An outer handler the same error crosses adds nothing."""
        recorded = failure.remember(error, dto, self._bus, handler, layer)
        if recorded is None:
            return
        span_error(span, error, recorded.error_at)
        record_error(
            error,
            type(dto).__name__,
            layer,
            self.identity,
            ignored_exceptions=self.ignored_errors,
            details={**self.logger.logger_fields, **recorded.fields()},
        )

    def escaped(self, error: BaseException, dto: object) -> None:
        """Context: called by the outermost bus only, so an error is logged once. An expected
        error — kept out of GlitchTip with ``ignore`` — is traffic: info, no traceback."""
        recorded = failure.failure_of(error)
        if recorded is not None:
            failure.annotate(error, recorded)
        summary, fields = self._describe(error, dto)
        if self._is_expected(error) or (recorded and self._is_expected(recorded.error)):
            self.logger.info(summary, **fields)
        else:
            self.logger.error(summary, exc_info=error, **fields)

    def handled(self, error: BaseException, dto: object) -> None:
        """Context: an error handler answered instead of raising; the caller never sees the
        error, so this line is its only trace."""
        summary, fields = self._describe(error, dto)
        message = f"{summary} (answered by an error handler)"
        if self._is_expected(error):
            self.logger.info(message, **fields)
        else:
            self.logger.warning(message, **fields)

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


class ProcessObservability:
    """What the transport needs, and nothing a bounded context already has."""

    @property
    def identity(self) -> ObservabilityIdentity:
        """Who this process is: ``artifact:version``, without a bus segment.

        Prefers what a bus already announced, so the transport and the buses of one
        deployment never disagree about the version that is running.
        """
        announced = registry.process_identity()
        if announced is not None:
            return announced
        resolved = resolve_identity("", module_name=caller_module())
        return resolved.model_copy(update={"bus": ""})

    @property
    def status(self) -> ComponentStatus:
        """Who owns the global provider that transport instrumentation will find.

        ``on:installed`` — this framework put the process provider there.
        ``on:host`` — someone got there first (Odoo, an operator's auto-instrumentation).
        ``off:*`` — nothing is registered, so transport spans would be no-ops.
        """
        if not OTEL_AVAILABLE:
            return off("sdk_missing")
        if registry.tracer_provider(PROCESS) is not None:
            return on("installed")
        if host_provider_is_real():
            return on("host")
        return off("no_endpoint")

    def tracer(self, instrumentation_name: str) -> Optional[Any]:
        """Tracer for a transport span. The global one: this process, or the host.

        Returns ``None`` when opentelemetry is not installed, so a caller can skip
        instrumenting instead of guarding the import itself.
        """
        try:
            from opentelemetry import trace

            return trace.get_tracer(instrumentation_name)
        except Exception:
            return None

    def bind_logger(self, logger: Any) -> None:
        """Make a process logger stamp the ids of the request being served.

        Without this an access log line, or a line from uvicorn, has no trace_id and
        cannot be found from the trace it belongs to. Never raises: a logger that
        does not support it simply keeps logging.
        """
        try:
            logger.add_context_source(self.trace_ids)
        except Exception:
            return

    def trace_ids(self) -> dict:
        """``trace_id``/``span_id`` of the active span, for a process logger.

        Hand it to ``logger.add_context_source(...)`` so an access log line, or a
        line from uvicorn, lands on the same trace as the request that caused it.
        """
        return current_otel_context()

    def was_reported(self, error: BaseException) -> bool:
        """Whether a bus already logged ``error`` on its way out.

        A transport that catches what a bus raised answers the caller, but logging it again
        would put the same failure in the logs twice. What no bus saw — a malformed
        request, a failure before any Feature ran — is still the transport's to log.
        """
        return failure.was_reported(error)

    def record_error(self, error: Exception, layer: str = "process") -> None:
        """Report a transport failure under the process release. Never raises."""
        record_error(error, "", layer, self.identity, kind="instance")


process = ProcessObservability()
