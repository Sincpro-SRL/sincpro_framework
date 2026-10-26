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
    declared_exporters,
    describing_attributes,
    failed,
    failure_of,
    framework_version,
    off,
    on,
    otel_sdk_disabled,
)
from sincpro_framework.observability.registry import PROCESS, observability_registry
from sincpro_framework.sincpro_conf import settings

try:
    import opentelemetry  # noqa: F401 — existence check, the only one in the package

    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False

PROXY_PROVIDERS = ("ProxyTracerProvider", "NoOpTracerProvider")


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


def current_otel_context() -> dict:
    """``trace_id``/``span_id`` of the active span, for the logger to pick up.

        current_otel_context()   ->  {"trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
                                      "span_id": "00f067aa0ba902b7"}      # inside a span
        current_otel_context()   ->  {}                       # no valid span, or no OTel

    The ids as hex, zero-padded to 32 and 16 characters; never raises.
    """
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


def tracer_for(bus: str, instrumentation_name: str = "sincpro_framework") -> Any:
    """The tracer whose spans carry this bus's ``service.name``.

    Falls back to the global provider so a bus can ride on the host's OTel setup.
    A provider this framework built for a *different* bus is never borrowed: a span
    exported under another service's name is worse than no span at all.

        tracer_for("billing")   ->  billing's provider's tracer, scope "sincpro_framework"
        tracer_for("sales")     ->  the host's tracer, when sales has no provider (Odoo)
        tracer_for("sales")     ->  None, when the global provider is one built for a bus

    1. The framework's version, the instrumentation scope's version.
    2. A provider registered for `bus` gives the tracer.
    3. Otherwise the global provider:
       3.1 one this framework built and registered (for a bus or the process): `None`;
       3.2 any other — the host's, or OTel's proxy — gives the tracer.
    Final: `None` when anything on the way raises.
    """
    try:
        # The scope's version says which framework release instrumented the span.
        version = framework_version()
        provider = observability_registry.tracer_provider(bus)
        if provider is not None:
            return provider.get_tracer(instrumentation_name, version)

        from opentelemetry import trace

        host = trace.get_tracer_provider()
        if observability_registry.owns_tracer_provider(host):
            return None
        return host.get_tracer(instrumentation_name, version)
    except Exception:
        return None


def _build_provider(identity: ObservabilityIdentity, endpoint: str) -> Any:
    """A tracer provider exporting to `endpoint` under `identity` — built, not installed: the
    caller decides whether it becomes the global one.

    1. The resource: `service.name` stable across releases, the version, release and tenant
       beside it — merged with `OTEL_RESOURCE_ATTRIBUTES` and the SDK's defaults.
    2. Sampling follows the parent; a root span is sampled at `otlp_traces_sample_rate`.
    Final: a batch processor on the OTLP exporter for `endpoint`.
    """
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.trace.sampling import ParentBased

    from sincpro_framework.observability.otlp import span_exporter

    provider = TracerProvider(
        # ``create`` merges OTEL_RESOURCE_ATTRIBUTES and the SDK defaults; the bare
        # constructor would drop tenant/env and telemetry.sdk.* from every span.
        resource=Resource.create(
            {SERVICE_NAME: identity.service_name, **describing_attributes(identity)}
        ),
        sampler=ParentBased(root=_root_sampler(settings.otlp_traces_sample_rate)),
    )
    # The endpoint must be passed explicitly: without it the SDK ignores our conf and
    # falls back to its own env var or localhost:4317.
    provider.add_span_processor(BatchSpanProcessor(span_exporter(endpoint)))
    return provider


def _bind_log_ids(logger: LoggerProxy | None) -> None:
    """Teach the logger to stamp the active span's ids on every line. Never raises."""
    if logger is None:
        return
    try:
        logger.add_context_source(current_otel_context)
    except Exception:
        return


def install_process_provider(identity: ObservabilityIdentity, endpoint: str) -> Any:
    """Own the global provider with the **process** identity, never a bus's.

    Transport instrumentation — ASGI, httpx, uvicorn — asks OTel for the global
    tracer. If a bus answered there, every HTTP request would be exported as if it
    belonged to that bounded context. A host that already owns the global is left
    alone: inside Odoo there is nothing for us to install.

        install_process_provider(billing_identity, "http://collector:4317")
            ->  TracerProvider(service.name="billing-sdk"), now the global, bus ""
        install_process_provider(...)   ->  None inside Odoo — its provider stays

    1. A real global provider already registered (anything but OTel's proxy or no-op):
       `None`, nothing installed.
    2. The process provider already built is reused; else one is built for the identity
       with its bus emptied, and both it and that identity are registered for the process.
    Final: it is set as the global provider and returned.
    """
    from opentelemetry import trace

    if type(trace.get_tracer_provider()).__name__ not in PROXY_PROVIDERS:
        return None

    provider = observability_registry.tracer_provider(PROCESS)
    if provider is None:
        process_identity = identity.model_copy(update={"bus": ""})
        provider = _build_provider(process_identity, endpoint)
        observability_registry.register_tracer_provider(PROCESS, provider)
        observability_registry.register_process_identity(process_identity)
    trace.set_tracer_provider(provider)
    return provider


def _turned_off() -> str:
    """Why OpenTelemetry's own variables forbid a provider of the framework's — `""` when they
    don't. `OTEL_SDK_DISABLED=true`, or `OTEL_TRACES_EXPORTER` naming no `otlp` (`none`)."""
    if otel_sdk_disabled():
        return "sdk_disabled"
    exporters = declared_exporters(getattr(settings, "otel_traces_exporter", None))
    if exporters and "otlp" not in exporters:
        return "exporter_none"
    return ""


def setup_tracing(
    identity: ObservabilityIdentity, logger: LoggerProxy | None = None
) -> ComponentStatus:
    """Install this bus's TracerProvider and bind trace ids to its logger.

        setup_tracing(identity, logger)   ->  on("init")       # otlp_endpoint set
        setup_tracing(identity, logger)   ->  on("host")       # no endpoint, inside Odoo
        setup_tracing(identity, logger)   ->  off("no_endpoint")
        setup_tracing(identity, logger)   ->  off("sdk_missing")   # no opentelemetry-sdk

    1. The OpenTelemetry SDK must import: `off("sdk_missing")` when it is not installed.
    2. No `otlp_endpoint`, or OpenTelemetry's own variables turn it off (`_turned_off`):
       2.1 a real host provider: the logger learns the span ids — `on("host")`;
       2.2 none: `off` with the reason — `sdk_disabled`, `exporter_none`, `no_endpoint`.
    3. The provider of this bus: the registered one, else built and registered —
       `failed("exporter_missing")` when the OTLP exporter cannot be imported.
    4. The process provider is installed as the global, when nobody owns it
       (`install_process_provider`).
    5. The logger learns the span ids (`_bind_log_ids`).
    Final: `on("init")`; any other exception is the status (`failure_of`), never raised.
    """
    try:
        import opentelemetry.sdk.trace  # noqa: F401 — the SDK must be installed
    except ImportError:
        return off("sdk_missing")
    except Exception as exc:
        return failure_of(exc)

    endpoint: str | None = settings.otlp_endpoint
    turned_off = _turned_off()
    if not endpoint or turned_off:
        if host_provider_is_real():
            # Riding the host's provider still produces spans, so the logger must
            # still learn where to read their ids from. The host's own switches are its.
            _bind_log_ids(logger)
            return on("host")
        return off(turned_off or "no_endpoint")

    try:
        provider = observability_registry.tracer_provider(identity.bus)
        if provider is None:
            try:
                provider = _build_provider(identity, endpoint)
            except ImportError:
                return failed("exporter_missing")
            observability_registry.register_tracer_provider(identity.bus, provider)

        install_process_provider(identity, endpoint)

        _bind_log_ids(logger)
        return on("init")
    except Exception as exc:
        return failure_of(exc)
