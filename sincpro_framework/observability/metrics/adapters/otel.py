"""`OtelRecorder`: the OpenTelemetry meter — whatever exporter the process configured (OTLP to a
collector, the OTel Prometheus reader, a vendor's) — `[opentelemetry]`.

    metrics.use(OtelRecorder())                      # the global meter provider
    metrics.use(OtelRecorder(meter_provider=mine))   # one of the project's

Context: the same rule as the tracer: a meter provider the host registered (Odoo, an operator's
auto-instrumentation) is used as it is, never replaced; with none and an OTLP endpoint set,
`for_process()` installs one under the **process** identity, not a bus's — its artifact as a
stable `service.name`, the version and the tenant beside it (`metrics_resource`) — and
every bus of the process records into it, told apart by `sincpro.context`.
"""

import threading
from collections.abc import Mapping
from typing import Any

from sincpro_framework.observability import process
from sincpro_framework.observability.domain import (
    ObservabilityIdentity,
    describing_attributes,
)
from sincpro_framework.observability.metrics.domain.instruments import (
    Instrument,
    InstrumentKind,
)
from sincpro_framework.observability.metrics.domain.recorder import Recorder
from sincpro_framework.observability.otlp import metric_exporter
from sincpro_framework.sincpro_conf import settings

OTEL_MISSING = "OpenTelemetry is not installed. Install with: pip install sincpro-framework[opentelemetry]"
PROXY_PROVIDERS = ("_ProxyMeterProvider", "ProxyMeterProvider", "NoOpMeterProvider")
INSTRUMENTATION = "sincpro_framework"


def host_meter_provider_is_real() -> bool:
    """Whether someone registered a real meter provider — not the API's proxy or no-op
    stand-in. `False` without opentelemetry.

    Context: a provider the host set (Odoo, auto-instrumentation) is used and never replaced.
    """
    try:
        import opentelemetry.metrics

        return (
            type(opentelemetry.metrics.get_meter_provider()).__name__ not in PROXY_PROVIDERS
        )
    except Exception:
        return False


def metrics_resource(identity: ObservabilityIdentity) -> dict[str, str]:
    """The resource of the process's meter provider: a stable service name, its version and
    tenant as attributes of their own — only what is set, and never over what the deployment
    declared in `OTEL_RESOURCE_ATTRIBUTES`."""
    return {"service.name": identity.service, **describing_attributes(identity)}


def install_otlp_meter_provider(endpoint: str, attributes: dict[str, str]) -> Any:
    """A meter provider exporting to `endpoint` every 60 s, set as the global one — only when
    nobody registered one first. Answers the global provider either way.

        install_otlp_meter_provider("http://collector:4317", {"service.name": "sincpro-odoo"})
            ->  MeterProvider(resource=service.name="sincpro-odoo" + OTEL_RESOURCE_ATTRIBUTES)
        install_otlp_meter_provider(...)   ->  the host's provider, untouched, inside Odoo

    The resource is `Resource.create`d — the deployment's attributes and the SDK defaults
    merged in; the exporter is built for `endpoint` explicitly (`metric_exporter`)."""
    import opentelemetry.metrics

    if host_meter_provider_is_real():
        return opentelemetry.metrics.get_meter_provider()
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource

    provider = MeterProvider(
        resource=Resource.create(attributes),
        metric_readers=[PeriodicExportingMetricReader(metric_exporter(endpoint))],
    )
    opentelemetry.metrics.set_meter_provider(provider)
    return provider


class OtelRecorder(Recorder):
    """Records into an OpenTelemetry meter — the instrument for each name created once and
    cached; the labels go as the measurement's attributes."""

    def __init__(self, meter_provider: Any | None = None) -> None:
        """A meter of `meter_provider`, else of the global one. Raises `ImportError` with the
        install hint when opentelemetry is missing — choosing this recorder without the extra
        is a mistake to see at once.

            OtelRecorder()                      ->  meter "sincpro_framework" of the global
            OtelRecorder(meter_provider=mine)   ->  meter "sincpro_framework" of `mine`
            OtelRecorder()                      ->  ImportError(OTEL_MISSING)   # no extra

        No instrument exists yet: each one is created on its first measurement."""
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
        installs under the process identity.

            OtelRecorder.for_process()   ->  OtelRecorder on a provider installed for
                                             `process.identity` (otlp_endpoint set, no host)
            OtelRecorder.for_process()   ->  OtelRecorder on the host's provider (Odoo)

        Without an endpoint nothing is installed: the recorder rides the global provider."""
        if settings.otlp_endpoint and not host_meter_provider_is_real():
            install_otlp_meter_provider(
                settings.otlp_endpoint, metrics_resource(process.identity)
            )
        return cls()

    def _instrument(self, instrument: Instrument) -> Any:
        """The OpenTelemetry instrument for `instrument`'s name — created the first time,
        reused after.

        1. A known name answers without the lock — the common path.
        2. Under the lock, checked again: a racing caller may have created it.
        3. Created by kind: a counter, an up-down counter, or a histogram with its buckets as
           an advisory; an API older than the advisory gets a histogram without it.
        Final: the instrument, cached under its name.
        """
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
        """`add` on the counter or up-down counter, the labels as attributes."""
        self._instrument(instrument).add(value, attributes=dict(labels))

    def record(self, instrument: Instrument, value: float, labels: Mapping[str, str]) -> None:
        """`record` on the histogram, the labels as attributes."""
        self._instrument(instrument).record(value, attributes=dict(labels))
