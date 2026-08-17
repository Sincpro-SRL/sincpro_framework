"""OTLP TracerProvider auto-configuration."""

from typing import Any

from sincpro_log.logger import LoggerProxy

from ..sincpro_conf import settings
from .status import ComponentStatus, component_status, status_from_exception

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


def _host_provider_is_real() -> bool:
    try:
        from opentelemetry import trace

        current_type = type(trace.get_tracer_provider()).__name__
        return current_type not in ("ProxyTracerProvider", "NoOpTracerProvider")
    except Exception:
        return False


def setup_otlp_provider(
    service_name: str, logger: LoggerProxy | None = None
) -> ComponentStatus:
    """Configure a dedicated TracerProvider for this bounded context.

    Never raises. Missing SDK or endpoint is ``off``. A host that already
    registered a real TracerProvider is ``on:host`` even without our endpoint.
    """
    global _sincpro_provider

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import ALWAYS_ON, ParentBased
    except ImportError:
        return component_status(False, "off", "sdk_missing")
    except Exception as exc:
        return status_from_exception(exc)

    endpoint: str | None = settings.otlp_endpoint
    if not endpoint:
        if _host_provider_is_real():
            return component_status(True, "on", "host")
        return component_status(False, "off", "no_endpoint")

    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    except ImportError:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        except ImportError:
            return component_status(False, "failed", "exporter_missing")
        except Exception as exc:
            return status_from_exception(exc)
    except Exception as exc:
        return status_from_exception(exc)

    try:
        provider = TracerProvider(
            resource=Resource(attributes={SERVICE_NAME: service_name}),
            sampler=ParentBased(root=ALWAYS_ON),
        )
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        _sincpro_provider = provider

        current_type: str = type(trace.get_tracer_provider()).__name__
        if current_type in ("ProxyTracerProvider", "NoOpTracerProvider"):
            trace.set_tracer_provider(provider)

        if logger is not None:
            logger.set_getter_context(_get_current_otel_context)
        return component_status(True, "on", "init")
    except Exception as exc:
        return status_from_exception(exc)


def get_framework_tracer(instrumentation_name: str) -> Any:
    """Return a Tracer from sincpro's private provider, or the global fallback.

    Using the private provider (when setup_otlp_provider was called) ensures spans
    carry sincpro's own service.name rather than inheriting the host application's.
    Falls back to the global provider when running without explicit OTel setup —
    covers the case where the host already configured a provider that sincpro should
    just piggyback on.
    """
    try:
        if _sincpro_provider is not None:
            return _sincpro_provider.get_tracer(instrumentation_name)
        from opentelemetry import trace as otel_trace

        return otel_trace.get_tracer(instrumentation_name)
    except Exception:
        return None
