"""Which recorder the process measures into: chosen from the configuration, once (PRD_03 §4.5).

    OTEL_METRICS_EXPORTER=otlp           # the OTel meter: the host's provider, or OTLP
    OTEL_METRICS_EXPORTER=prometheus     # prometheus_client, /metrics
    OTEL_METRICS_EXPORTER=none           # nothing — as does OTEL_SDK_DISABLED=true
    (not set)                            # the default: OTel when it is configured, else off
    SINCPRO_METRICS_BACKEND=otel|prometheus|off — the framework's own name for the same switch:
    set to anything but `auto`, it wins.

    active_recorder.get()     the recorder in use — `from_settings()` the first time
    active_recorder.emit(issued, 1, {"currency": "BOB"})

Context: choosing an adapter is this package's decision, so it is made here; the shield every
measurement passes through is `infrastructure.active`. OpenTelemetry and prometheus_client are
optional extras: their adapters are imported only when the configuration names them.
"""

from importlib.util import find_spec

from sincpro_framework.observability.domain import declared_exporters, otel_sdk_disabled
from sincpro_framework.observability.metrics.domain.recorder import Recorder
from sincpro_framework.observability.metrics.infrastructure.active import ActiveRecorder
from sincpro_framework.sincpro_conf import settings


def _installed(module: str) -> bool:
    """Whether `module` can be imported, without importing it — `False` when blocked."""
    try:
        return find_spec(module) is not None
    except (ImportError, ValueError):  # blocked, or a module without a spec
        return False


def _otel_is_configured() -> bool:
    """Whether `auto` should pick the OTel recorder: the SDK is installed, and either an OTLP
    endpoint is set or a host registered a real meter provider."""
    if not (_installed("opentelemetry") and _installed("opentelemetry.sdk")):
        return False
    if settings.otlp_endpoint:
        return True
    from sincpro_framework.observability.metrics.adapters.otel import (
        host_meter_provider_is_real,
    )

    return host_meter_provider_is_real()


def _standard_backend() -> str:
    """What OpenTelemetry's own variables ask for, when `SINCPRO_METRICS_BACKEND` asks nothing:
    `auto`, `otel`, `prometheus` or `off`.

    Context: an operator who knows OpenTelemetry turns metrics off with `OTEL_METRICS_EXPORTER=
    none` or `OTEL_SDK_DISABLED=true`, as the traces already are. An exporter the framework does
    not build (`console`) is off too — nothing would be sent.
    """
    if otel_sdk_disabled():
        return "off"
    exporters = declared_exporters(getattr(settings, "otel_metrics_exporter", None))
    if not exporters:
        return "auto"
    if "otlp" in exporters:
        return "otel"
    if "prometheus" in exporters:
        return "prometheus"
    return "off"


def from_settings() -> Recorder | None:
    """The recorder the settings and the environment ask for — `None` when metrics are off.

    1. `off`: none.
    2. `prometheus`: `PrometheusRecorder` on the default registry — `[prometheus]`.
    3. `otel`: `OtelRecorder` on the host's meter provider, or one this installs for OTLP.
    Final: `auto` is `otel` when a meter provider or an OTLP endpoint is there, else none —
    Prometheus is never chosen by guessing, because it needs its `/metrics` served.
    `SINCPRO_METRICS_BACKEND` decides when set; else `OTEL_METRICS_EXPORTER` and
    `OTEL_SDK_DISABLED` (`_standard_backend`).

        SINCPRO_METRICS_BACKEND=prometheus                ->  PrometheusRecorder()
        OTEL_METRICS_EXPORTER=prometheus                  ->  PrometheusRecorder()
        OTEL_METRICS_EXPORTER=otlp                        ->  OtelRecorder.for_process()
        OTEL_METRICS_EXPORTER=none                        ->  None
        nothing set, no OTLP endpoint, no host provider   ->  None

    Never a guess beyond that: an exporter the framework does not build, or an unknown backend
    name, leaves metrics off."""
    backend = (getattr(settings, "metrics_backend", None) or "auto").lower()
    if backend == "auto":
        backend = _standard_backend()
    if backend == "off":
        return None
    if backend == "prometheus":
        from sincpro_framework.observability.metrics.adapters.prometheus import (
            PrometheusRecorder,
        )

        return PrometheusRecorder()
    if backend == "otel" or (backend == "auto" and _otel_is_configured()):
        from sincpro_framework.observability.metrics.adapters.otel import OtelRecorder

        return OtelRecorder.for_process()
    return None


active_recorder = ActiveRecorder(from_settings)
"""The one recorder of the process — what every measurement, the facade and the bus's
`Observability` record through."""
