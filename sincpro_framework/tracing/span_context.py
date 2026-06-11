"""
FrameworkSpanContext — context manager returned by UseFramework.with_trace().

Responsibilities:
  1. Log correlation: binds trace_id/span_id to the shared LoggerProxy so all
     internal bus logs carry those fields automatically.
  2. Framework context: injects trace_id/span_id into the framework context dict
     so Feature/ApplicationService can read them via self.context.get("trace_id").
  3. OTel context (when installed): attaches a parent span context so all bus
     spans become children of the correct parent trace.

All OTel imports are deferred inside methods — the module is always importable
regardless of whether opentelemetry is installed.
"""

from typing import TYPE_CHECKING, Any, Mapping, Optional
from uuid import uuid4

try:
    import opentelemetry  # noqa: F401 — existence check only

    _OTEL_AVAILABLE = True
except ImportError:
    _OTEL_AVAILABLE = False

if TYPE_CHECKING:
    from ..use_bus import UseFramework


class FrameworkSpanContext:
    """Context manager that sets up tracing for a UseFramework execution block."""

    def __init__(
        self,
        framework: "UseFramework",
        service_name: str,
        trace_id: Optional[str] = None,
        span_id: Optional[str] = None,
        carrier: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.framework = framework
        self._service_name = service_name
        self._trace_id = trace_id
        self._span_id = span_id
        self._carrier = carrier

        self._otel_token: Any = None
        self._root_span: Any = None
        self._log_ctx_cm: Any = None
        self._prev_context: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Context manager protocol
    # ------------------------------------------------------------------

    def __enter__(self) -> "UseFramework":
        resolved_trace_id, resolved_span_id = self._setup_trace_context()

        # Bind to shared LoggerProxy — propagates to FeatureBus,
        # ApplicationServiceBus and FrameworkBus automatically because they all
        # share the same LoggerProxy instance.
        self._log_ctx_cm = self.framework.logger.context(
            trace_id=resolved_trace_id, span_id=resolved_span_id
        )
        try:
            self._log_ctx_cm.__enter__()
        except Exception:
            # Logger failed — clean up the OTel context we already attached so
            # it doesn't leak into subsequent requests on this worker thread.
            self._cleanup_otel()
            raise

        # Make trace_id/span_id available inside Feature/ApplicationService
        # via self.context.get("trace_id") without any extra imports.
        self._prev_context = self.framework._get_context().copy()
        merged = {
            **self._prev_context,
            "trace_id": resolved_trace_id,
            "span_id": resolved_span_id,
        }
        self.framework._set_context(merged)
        self.framework._inject_context_to_services_and_features(merged)

        return self.framework

    def _cleanup_otel(self) -> None:
        """Detach the OTel context token and end the root span if present.

        Called on __enter__ failure so the OTel context doesn't leak into
        subsequent requests on this worker thread.
        """
        if _OTEL_AVAILABLE:
            from opentelemetry import context as otel_context

            if self._root_span is not None:
                self._root_span.end()
                self._root_span = None
            if self._otel_token is not None:
                otel_context.detach(self._otel_token)
                self._otel_token = None

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        if _OTEL_AVAILABLE:
            from opentelemetry import context as otel_context

            if self._root_span is not None:
                if exc_type is not None:
                    from opentelemetry.trace import StatusCode

                    self._root_span.set_status(StatusCode.ERROR, str(exc_val))
                self._root_span.end()

            if self._otel_token is not None:
                otel_context.detach(self._otel_token)

        if self._log_ctx_cm is not None:
            self._log_ctx_cm.__exit__(exc_type, exc_val, exc_tb)

        self.framework._set_context(self._prev_context)
        self.framework._inject_context_to_services_and_features(self._prev_context)

        return False

    # ------------------------------------------------------------------
    # Internal helpers — each case is isolated, OTel imports are deferred
    # ------------------------------------------------------------------

    def _setup_trace_context(self) -> tuple[str, str]:
        """Resolve trace_id/span_id and attach OTel context when available."""
        if not _OTEL_AVAILABLE:
            if self._carrier is not None:
                import warnings

                warnings.warn(
                    "with_trace(carrier=...) was called but opentelemetry is not installed. "
                    "The carrier will be ignored and new trace IDs will be generated. "
                    "Install sincpro_framework[opentelemetry] to enable W3C trace propagation.",
                    RuntimeWarning,
                    stacklevel=3,
                )
            return self._trace_id or str(uuid4()), self._span_id or str(uuid4())

        if self._carrier is not None:
            return self._setup_from_carrier()

        if self._trace_id is not None:
            return self._setup_from_explicit_ids()

        return self._setup_root_span()

    def _setup_from_carrier(self) -> tuple[str, str]:
        """Extract W3C traceparent from carrier headers and attach context."""
        from opentelemetry import context as otel_context
        from opentelemetry import trace as otel_trace
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

        ctx = TraceContextTextMapPropagator().extract(carrier=self._carrier)
        span = otel_trace.get_current_span(ctx)
        span_ctx = span.get_span_context()

        if span_ctx.is_valid:
            self._otel_token = otel_context.attach(ctx)
            return format(span_ctx.trace_id, "032x"), format(span_ctx.span_id, "016x")

        # Extraction failed (missing / invalid header) — fall back to a fresh root span
        return self._setup_root_span()

    def _setup_from_explicit_ids(self) -> tuple[str, str]:
        """Construct a NonRecordingSpan from explicit hex trace_id/span_id."""
        from opentelemetry import context as otel_context
        from opentelemetry import trace as otel_trace

        trace_id = self._trace_id  # guaranteed non-None here
        span_id = self._span_id or str(uuid4())
        try:
            from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

            span_ctx = SpanContext(
                trace_id=int(trace_id, 16),  # type: ignore[arg-type]
                span_id=int(span_id, 16),
                is_remote=True,
                trace_flags=TraceFlags(TraceFlags.SAMPLED),
            )
            ctx = otel_trace.set_span_in_context(NonRecordingSpan(span_ctx))
            self._otel_token = otel_context.attach(ctx)
        except ValueError:
            # IDs are not hex (e.g. plain UUIDs from sincpro_log) — skip OTel
            # attachment; log correlation still works with the provided strings.
            pass
        return trace_id, span_id  # type: ignore[return-value]

    def _setup_root_span(self) -> tuple[str, str]:
        """Create a root container span for this execution block.

        The span name is the UseFramework instance name (bundled_context_name).
        All bus spans (FeatureBus, ApplicationServiceBus) become its children
        because OTel uses contextvars to propagate the active span automatically.

        This ensures trace_id in logs matches the OTel trace_id in exported spans.
        """
        from opentelemetry import context as otel_context
        from opentelemetry import trace as otel_trace

        tracer = otel_trace.get_tracer("sincpro_framework")
        self._root_span = tracer.start_span(self._service_name)
        ctx = otel_trace.set_span_in_context(self._root_span)
        self._otel_token = otel_context.attach(ctx)

        span_ctx = self._root_span.get_span_context()
        if span_ctx.is_valid:
            return format(span_ctx.trace_id, "032x"), format(span_ctx.span_id, "016x")

        # OTel installed but provider is no-op (no SDK configured) →
        # fall back to UUID-based correlation so logs always have trace_id.
        return str(uuid4()), str(uuid4())
