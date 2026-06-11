"""OTLP TracerProvider auto-configuration."""

from ..sincpro_conf import settings


def setup_otlp_provider(service_name: str) -> None:
    """Configure a TracerProvider with OTLP export for this bounded context.

    Runs only when ALL of the following are true:
    - opentelemetry-sdk is installed
    - ``otlp_endpoint`` is set in the framework config (or via OTEL_EXPORTER_OTLP_ENDPOINT)
    - No real provider is already registered (avoids double-config)

    Important: the OTel TracerProvider is a **process-level singleton**. Only the
    first ``UseFramework`` instance that calls ``build_root_bus()`` registers it.
    Subsequent instances skip registration because the provider is already active.

    The ``service.name`` Resource attribute will reflect the *first* bounded context
    that registered. To distinguish spans from different bounded contexts, use the
    ``sincpro.instance`` span attribute (set automatically on every span).
    """
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        return

    endpoint = settings.otlp_endpoint
    if not endpoint:
        return

    current_type = type(trace.get_tracer_provider()).__name__
    if current_type not in ("ProxyTracerProvider", "NoOpTracerProvider"):
        return

    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    except ImportError:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        except ImportError:
            return

    provider = TracerProvider(resource=Resource(attributes={SERVICE_NAME: service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
