"""`OtelRecorder`: the OpenTelemetry meter — whatever exporter the process configured (OTLP to a
collector, the OTel Prometheus reader, a vendor's) — `[opentelemetry]`.

    metrics.use(OtelRecorder())                      # the global meter provider
    metrics.use(OtelRecorder(meter_provider=mine))   # one of the project's

Context: the same rule as the tracer: a meter provider the host registered (Odoo, an operator's
auto-instrumentation) is used as it is, never replaced; with none and an OTLP endpoint set,
`for_process()` installs one under the **process** identity, not a bus's — its artifact as a
stable `service.name`, the version and the tenant beside it (`identity.metrics_resource`) — and
every bus of the process records into it, told apart by `sincpro.context`.
"""

import threading
from collections.abc import Mapping
from typing import Any

from sincpro_framework.observability.metrics.domain.instruments import (
    Instrument,
    InstrumentKind,
)
from sincpro_framework.observability.metrics.domain.recorder import Recorder

OTEL_MISSING = "OpenTelemetry is not installed. Install with: pip install sincpro-framework[opentelemetry]"
PROXY_PROVIDERS = ("_ProxyMeterProvider", "ProxyMeterProvider", "NoOpMeterProvider")
INSTRUMENTATION = "sincpro_framework"


def host_meter_provider_is_real() -> bool:
    try:
        import opentelemetry.metrics

        return (
            type(opentelemetry.metrics.get_meter_provider()).__name__ not in PROXY_PROVIDERS
        )
    except Exception:
        return False


def install_otlp_meter_provider(endpoint: str, attributes: dict[str, str]) -> Any:
    """A meter provider exporting to `endpoint` every 60 s, set as the global one — only when
    nobody registered one first. Answers the global provider either way."""
    import opentelemetry.metrics

    if host_meter_provider_is_real():
        return opentelemetry.metrics.get_meter_provider()
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource

    try:
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
    except ImportError:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

    provider = MeterProvider(
        resource=Resource.create(attributes),
        metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=endpoint))],
    )
    opentelemetry.metrics.set_meter_provider(provider)
    return provider


class OtelRecorder(Recorder):
    def __init__(self, meter_provider: Any | None = None) -> None:
        try:
            import opentelemetry.metrics
        except ImportError as error:
            raise ImportError(OTEL_MISSING) from error
        provider = meter_provider or opentelemetry.metrics.get_meter_provider()
        self._meter = provider.get_meter(INSTRUMENTATION)
        self._lock = threading.Lock()
        self._instruments: dict[str, Any] = {}

    @classmethod
    def for_process(cls) -> "OtelRecorder":
        """On the host's provider; or, with an OTLP endpoint and none registered, on one this
        installs under the process identity."""
        from sincpro_framework.sincpro_conf import settings

        if settings.otlp_endpoint and not host_meter_provider_is_real():
            from sincpro_framework.observability import process
            from sincpro_framework.observability.metrics.infrastructure.identity import (
                metrics_resource,
            )

            install_otlp_meter_provider(
                settings.otlp_endpoint, metrics_resource(process.identity)
            )
        return cls()

    def _instrument(self, instrument: Instrument) -> Any:
        known = self._instruments.get(instrument.name)
        if known is not None:
            return known
        with self._lock:
            known = self._instruments.get(instrument.name)
            if known is not None:
                return known
            common: dict[str, Any] = {
                "name": instrument.name,
                "unit": instrument.unit,
                "description": instrument.description,
            }
            if instrument.kind == InstrumentKind.COUNTER:
                created = self._meter.create_counter(**common)
            elif instrument.kind == InstrumentKind.UP_DOWN:
                created = self._meter.create_up_down_counter(**common)
            else:
                try:
                    created = self._meter.create_histogram(
                        **common,
                        explicit_bucket_boundaries_advisory=(
                            list(instrument.buckets) if instrument.buckets else None
                        ),
                    )
                except TypeError:  # an API older than the bucket advisory
                    created = self._meter.create_histogram(**common)
            self._instruments[instrument.name] = created
            return created

    def add(self, instrument: Instrument, value: float, labels: Mapping[str, str]) -> None:
        self._instrument(instrument).add(value, attributes=dict(labels))

    def record(self, instrument: Instrument, value: float, labels: Mapping[str, str]) -> None:
        self._instrument(instrument).record(value, attributes=dict(labels))
