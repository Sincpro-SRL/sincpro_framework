"""`BusObservabilityMixin`: what a `UseFramework` mixes in to trace a block of executions and to
choose which errors GlitchTip never hears of."""

from typing import Any, Callable, Mapping, Optional, Type, cast

from sincpro_framework.observability.api import Observability
from sincpro_framework.observability.tracing.span_context import FrameworkSpanContext


class BusObservabilityMixin:
    """What a `UseFramework` adds to trace and report."""

    observability: Observability
    build_root_bus: Callable[[], None]

    def ignore_sentry_exceptions(self, *exc_types: Type[Exception]) -> None:
        """Do not send these exception types to GlitchTip / Sentry.

        Error handlers still run. Use this for expected domain errors
        (validation, insufficient funds, etc.) so they do not hide real bugs:
        anything not in this list is reported even if a handler swallows it.
        """
        self.observability.ignore(*exc_types)

    def with_trace(
        self,
        trace_id: Optional[str] = None,
        span_id: Optional[str] = None,
        carrier: Optional[Mapping[str, Any]] = None,
    ) -> FrameworkSpanContext:
        """Create a tracing context manager for an execution block.

        Binds trace_id/span_id to all internal logs and makes them available in
        Feature/ApplicationService via self.context.get("trace_id").

        When opentelemetry is installed, also attaches an OTel span context so
        all bus spans are exported under the correct parent trace.

        Args:
            trace_id: Explicit trace identifier (hex string for OTel compatibility,
                      or any string for log-only correlation). Auto-generated if None.
            span_id:  Explicit span identifier. Auto-generated if None.
            carrier:  Mapping with W3C traceparent/tracestate headers (e.g.
                      request.headers). Extracts the parent trace from the headers.
                      Requires opentelemetry to be installed.

        Returns:
            FrameworkSpanContext ready to use with the ``with`` statement.

        Examples::

            # Fresh trace — auto-generated IDs
            with app.with_trace() as traced:
                result = traced(MyDTO(...))

            # Propagate from explicit IDs
            with app.with_trace(trace_id="4bf92f35...", span_id="00f067aa...") as traced:
                result = traced(MyDTO(...))

            # Extract from incoming W3C headers (OTel)
            with app.with_trace(carrier=request.headers) as traced:
                result = traced(MyDTO(...))

            # Compose with context()
            with app.with_trace(carrier=headers) as traced:
                with traced.context({"user_id": "u-123"}) as app_with_ctx:
                    result = app_with_ctx(MyDTO(...))
        """
        self.build_root_bus()
        return self.observability.trace_context(
            cast(Any, self), trace_id=trace_id, span_id=span_id, carrier=carrier
        )

    def with_parent_trace(self) -> FrameworkSpanContext:
        """Adopt the currently active OTel span for log correlation and context injection.

        Unlike ``with_trace()``, this does **not** create a new root span. It reads
        the ``trace_id`` and ``span_id`` from whatever span is currently active in the
        process — typically a span created by the host application's instrumentation
        (Odoo WSGI middleware, FastAPI OpenTelemetry middleware, a Celery task decorator,
        etc.) — and uses them to correlate logs and make them available inside
        Feature / ApplicationService via ``self.context.get("trace_id")``.

        Because no new span is created and the OTel context is not modified, DTO spans
        produced by the bus are **direct children** of the active span, not grandchildren
        through an intermediate root span as ``with_trace()`` would produce::

            # with with_trace()
            host-span
              └── my-service (root span added by the framework)
                    └── CreateOrderDTO (application_service)

            # with with_parent_trace()
            host-span
              └── CreateOrderDTO (application_service)   ← direct child, no extra level

        Falls back to UUID-based log correlation when no valid OTel span is active or
        when opentelemetry is not installed, so it is always safe to call.

        Examples::

            # Inside an Odoo controller or FastAPI handler where the
            # host has already started a span
            with framework.with_parent_trace() as fw:
                result = fw(CreateOrderDTO(...), OrderResult)
        """
        self.build_root_bus()
        return self.observability.trace_context(cast(Any, self), adopt_active=True)
