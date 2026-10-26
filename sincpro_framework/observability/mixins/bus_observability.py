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

        A `ClientError` or a `DomainError` is already left out: it is written for the caller,
        traffic and not a bug. Use this for other expected errors — a library's own, a service's
        refusal the project treats as normal. Error handlers still run, and anything reported
        is reported even if a handler swallows it.
        """
        self.observability.ignore(*exc_types)

    def report_sentry_exceptions(self, *exc_types: Type[Exception]) -> None:
        """Send these exception types to GlitchTip / Sentry although they are expected — a
        `ClientError` or a `DomainError` the project wants to see, such as a fraud refusal."""
        self.observability.report(*exc_types)

    def with_trace(
        self,
        trace_id: Optional[str] = None,
        span_id: Optional[str] = None,
        carrier: Optional[Mapping[str, Any]] = None,
    ) -> FrameworkSpanContext:
        """Open a trace around a block — every use case run inside is a span of it.

        Context: binds `trace_id`/`span_id` to every internal log line and puts them in the
        context, so a Feature or ApplicationService reads `self.context.get("trace_id")`; with
        OpenTelemetry installed it also attaches an OTel span context, so the bus spans export
        under the right parent. The parent is, in order: the W3C `traceparent`/`tracestate` of
        a `carrier` (incoming headers — needs OpenTelemetry), an explicit `trace_id` (hex for
        OTel, any string for log-only correlation) with an optional `span_id`, else a new root
        with generated ids. The answer is used in a `with` statement.

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
