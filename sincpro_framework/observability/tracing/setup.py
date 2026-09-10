"""Bring OpenTelemetry up for one bus. Never raises. Never replaces the host's provider.

Each bus gets its own TracerProvider so its spans carry its own ``service.name``
(``artifact:version:bus``) even when the host application — Odoo, FastAPI, Celery —
already registered a global provider of its own.
"""

from typing import Any

from sincpro_log.logger import LoggerProxy

from sincpro_framework.observability.domain import (
    ComponentStatus,
    ObservabilityIdentity,
    failed,
    failure_of,
    off,
    on,
)
from sincpro_framework.observability.registry import registry
from sincpro_framework.sincpro_conf import settings

try:
    import opentelemetry  # noqa: F401 — existence check, the only one in the package

    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False

PROXY_PROVIDERS = ("ProxyTracerProvider", "NoOpTracerProvider")


def current_otel_context() -> dict:
    """``trace_id``/``span_id`` of the active span, for the logger to pick up."""
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


def host_provider_is_real() -> bool:
    """True when something other than OTel's no-op placeholder is registered globally."""
    try:
        from opentelemetry import trace

        return type(trace.get_tracer_provider()).__name__ not in PROXY_PROVIDERS
    except Exception:
        return False


def _root_sampler(ratio: float) -> Any:
    """How much to record when this bus starts a trace nobody else decided on.

    Wrapped in ``ParentBased`` by the caller: a decision already taken upstream —
    by Odoo, by an incoming traceparent — always wins over this ratio, so a
    sampled request is never truncated halfway through.
    """
    from opentelemetry.sdk.trace.sampling import ALWAYS_OFF, ALWAYS_ON, TraceIdRatioBased

    if ratio >= 1.0:
        return ALWAYS_ON
    if ratio <= 0.0:
        return ALWAYS_OFF
    return TraceIdRatioBased(ratio)


def _build_provider(identity: ObservabilityIdentity, endpoint: str) -> Any:
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.trace.sampling import ParentBased

    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    except ImportError:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    provider = TracerProvider(
        resource=Resource(attributes={SERVICE_NAME: identity.service_name}),
        sampler=ParentBased(root=_root_sampler(settings.otlp_traces_sample_rate)),
    )
    # The endpoint must be passed explicitly: without it the SDK ignores our conf and
    # falls back to its own env var or localhost:4317.
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    return provider


def setup(
    identity: ObservabilityIdentity, logger: LoggerProxy | None = None
) -> ComponentStatus:
    """Install this bus's TracerProvider and bind trace ids to its logger."""
    try:
        import opentelemetry.sdk.trace  # noqa: F401 — the SDK must be installed
        from opentelemetry import trace
    except ImportError:
        return off("sdk_missing")
    except Exception as exc:
        return failure_of(exc)

    endpoint: str | None = settings.otlp_endpoint
    if not endpoint:
        if host_provider_is_real():
            return on("host")
        return off("no_endpoint")

    try:
        provider = registry.tracer_provider(identity.bus)
        if provider is None:
            try:
                provider = _build_provider(identity, endpoint)
            except ImportError:
                return failed("exporter_missing")
            registry.register_tracer_provider(identity.bus, provider)

            if type(trace.get_tracer_provider()).__name__ in PROXY_PROVIDERS:
                trace.set_tracer_provider(provider)

        if logger is not None:
            logger.set_getter_context(current_otel_context)
        return on("init")
    except Exception as exc:
        return failure_of(exc)


def tracer_for(bus: str, instrumentation_name: str = "sincpro_framework") -> Any:
    """The tracer whose spans carry this bus's ``service.name``.

    Falls back to the global provider so a bus can ride on the host's OTel setup.
    A provider this framework built for a *different* bus is never borrowed: a span
    exported under another service's name is worse than no span at all.
    """
    try:
        provider = registry.tracer_provider(bus)
        if provider is not None:
            return provider.get_tracer(instrumentation_name)

        from opentelemetry import trace

        host = trace.get_tracer_provider()
        if registry.owns_tracer_provider(host):
            return None
        return host.get_tracer(instrumentation_name)
    except Exception:
        return None
