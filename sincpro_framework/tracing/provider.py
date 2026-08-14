"""OTLP TracerProvider auto-configuration."""

from typing import Any

from sincpro_log.logger import LoggerProxy

from ..sincpro_conf import settings

# Private provider owned by sincpro. Kept separate from the global OTel provider
# so sincpro spans carry their own service.name even when another framework
# (Odoo, FastAPI, Celery) already registered the global provider first.
_sincpro_provider: Any = None


def _get_current_otel_context() -> dict:
    """Return trace_id and span_id from the currently active OTel span, or {}."""
    try:
        from opentelemetry import trace

        span_ctx = trace.get_current_span().get_span_context()
        if span_ctx.is_valid:
            return {
                "trace_id": format(span_ctx.trace_id, "032x"),
                "span_id": format(span_ctx.span_id, "016x"),
            }
    except Exception:
        pass
    return {}


def setup_otlp_provider(service_name: str, logger: LoggerProxy | None = None) -> None:
    """Configure a dedicated TracerProvider for this bounded context.

    Always creates a private TracerProvider with the given ``service_name`` —
    this is what appears as ``service.name`` in the exported spans, making sincpro
    spans identifiable independently from the host application (Odoo, FastAPI, etc.).

    If no global provider is registered yet, the private provider is also set as
    the global so that direct ``framework(dto)`` calls (without ``with_trace()``)
    still produce real spans.

    If another framework already registered the global provider, the private provider
    is kept internal only. All sincpro spans will still be children of whatever span
    is active at call time — the trace_id and parent relationship are preserved via
    OTel contextvars, which are process-wide and independent of which provider
    created each span. In Tempo/Jaeger the full tree is visible:

        service.name=odoo      →  GET /web/dataset/call_kw
        service.name=<service_name>  →    └── CreateOrderDTO  (application_service)
        service.name=<service_name>  →         └── ValidateDTO  (feature)

    Sampler: ``ParentBased(root=ALWAYS_ON)`` — when a parent span exists (e.g. from
    Odoo), its sampling decision is respected automatically.  When there is no parent
    (standalone CLI, worker, test) every span is sampled.
    """
    global _sincpro_provider

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import ALWAYS_ON, ParentBased
    except ImportError:
        return

    endpoint: str | None = settings.otlp_endpoint
    if not endpoint:
        return

    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    except ImportError:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        except ImportError:
            return

    provider = TracerProvider(
        resource=Resource(attributes={SERVICE_NAME: service_name}),
        sampler=ParentBased(root=ALWAYS_ON),
    )
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    _sincpro_provider = provider

    # Register as global only when no real provider exists yet.
    current_type: str = type(trace.get_tracer_provider()).__name__
    if current_type in ("ProxyTracerProvider", "NoOpTracerProvider"):
        trace.set_tracer_provider(provider)

    # Wire OTel span context into the logger so every framework.logger.info(...)
    # call automatically carries trace_id/span_id from the active span.
    if logger is not None:
        logger.set_getter_context(_get_current_otel_context)


def get_framework_tracer(instrumentation_name: str) -> Any:
    """Return a Tracer from sincpro's private provider, or the global fallback.

    Using the private provider (when setup_otlp_provider was called) ensures spans
    carry sincpro's own service.name rather than inheriting the host application's.
    Falls back to the global provider when running without explicit OTel setup —
    covers the case where the host already configured a provider that sincpro should
    just piggyback on.
    """
    if _sincpro_provider is not None:
        return _sincpro_provider.get_tracer(instrumentation_name)

    try:
        from opentelemetry import trace as otel_trace

        return otel_trace.get_tracer(instrumentation_name)
    except ImportError:
        return None
