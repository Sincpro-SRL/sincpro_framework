"""The backends (PRD_03 §4.5): every recorder keeps the one contract, Prometheus is scraped with
its own names, OpenTelemetry exports what the bus measured, and the configuration picks one.

The incidents: a scrape that shows `sincpro.use_case.duration` Prometheus cannot parse, two
workers whose counters overwrite each other, a backend picked by guessing that nobody scrapes.
"""

from collections.abc import Iterator, Mapping
from decimal import Decimal

import pytest
from prometheus_client import CollectorRegistry

from sincpro_framework import Feature, UseFramework
from sincpro_framework.observability.metrics import InMemoryRecorder, metrics, of
from sincpro_framework.observability.metrics.adapters.otel import OtelRecorder
from sincpro_framework.observability.metrics.adapters.prometheus import (
    PrometheusRecorder,
    prometheus_name,
)
from sincpro_framework.observability.metrics.domain.instruments import (
    Instrument,
    InstrumentKind,
)
from sincpro_framework.observability.metrics.infrastructure import active as active_module
from sincpro_framework.observability.metrics.testing import RecorderContract

from .test_use_case_metrics import (
    CommandIssueInvoice,
    Currency,
    ResponseIssueInvoice,
    _billing,
)

# --- one contract, every backend ----------------------------------------------------------------


def test_the_in_memory_recorder_keeps_the_contract():
    recorder = InMemoryRecorder()

    def total(name: str, labels: Mapping[str, str]) -> float:
        return recorder.totals(name).get(tuple(sorted(labels.items())), 0)

    def count(name: str, labels: Mapping[str, str]) -> float:
        return sum(
            len(values) for found, values in recorder.observations(name) if found == labels
        )

    RecorderContract(recorder, total, count).check()


def _prometheus(registry: CollectorRegistry):  # type: ignore[no-untyped-def]
    def sample(name: str, suffix: str, labels: Mapping[str, str]) -> float:
        translated = {key.replace(".", "_"): value for key, value in labels.items()}
        value = registry.get_sample_value(name.replace(".", "_") + suffix, translated)
        return value or 0

    def total(name: str, labels: Mapping[str, str]) -> float:
        return sample(name, "_total", labels) or sample(name, "", labels)

    def count(name: str, labels: Mapping[str, str]) -> float:
        return sample(name, "_seconds_count", labels) or sample(name, "_count", labels)

    return total, count


def test_the_prometheus_recorder_keeps_the_contract():
    registry = CollectorRegistry()

    RecorderContract(PrometheusRecorder(registry), *_prometheus(registry)).check()


@pytest.fixture
def otel_reader():  # type: ignore[no-untyped-def]
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    reader = InMemoryMetricReader()
    return reader, OtelRecorder(meter_provider=MeterProvider(metric_readers=[reader]))


def _otel_points(reader, name: str, labels: Mapping[str, str]) -> list:  # type: ignore[no-untyped-def]
    data = reader.get_metrics_data()
    found = []
    for resource in data.resource_metrics if data else ():
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                if metric.name == name:
                    found += [
                        point
                        for point in metric.data.data_points
                        if dict(point.attributes) == dict(labels)
                    ]
    return found


def test_the_otel_recorder_keeps_the_contract(otel_reader):
    reader, recorder = otel_reader

    def total(name: str, labels: Mapping[str, str]) -> float:
        return sum(point.value for point in _otel_points(reader, name, labels))

    def count(name: str, labels: Mapping[str, str]) -> float:
        return sum(point.count for point in _otel_points(reader, name, labels))

    RecorderContract(recorder, total, count).check()


# --- Prometheus: the reference stack ------------------------------------------------------------


@pytest.fixture
def prometheus() -> Iterator[PrometheusRecorder]:
    recorder = PrometheusRecorder(CollectorRegistry())
    with metrics.using(recorder):
        yield recorder


def test_a_scrape_carries_prometheus_names_for_what_the_bus_measured(prometheus):
    bus = _billing()
    for _ in range(2):
        bus(
            CommandIssueInvoice(customer_id="c", currency=Currency.BOB, total=Decimal("2.5")),
            ResponseIssueInvoice,
        )

    scrape = prometheus.exposition().decode()

    assert 'billing_issue_invoice_runs_total{currency="BOB"} 2.0' in scrape
    assert 'billing_issue_invoice_total{currency="BOB"} 5.0' in scrape  # the summed `total`
    assert "sincpro_use_case_duration_seconds_bucket{" in scrape
    assert 'sincpro_context="billing"' in scrape and 'sincpro_outcome="ok"' in scrape


def test_a_duration_is_named_in_seconds_and_nothing_else_is():
    duration = Instrument(name="billing.pricing", kind=InstrumentKind.HISTOGRAM, unit="s")
    amount = Instrument(name="billing.amount", kind=InstrumentKind.HISTOGRAM, unit="BOB")

    assert (prometheus_name(duration), prometheus_name(amount)) == (
        "billing_pricing_seconds",
        "billing_amount",
    )


def test_one_name_declared_twice_with_other_labels_is_dropped_not_raised(prometheus):
    """Prometheus raises on it; the use case must not."""
    first = Instrument(name="billing.runs", kind=InstrumentKind.COUNTER, label_keys=("a",))
    again = Instrument(name="billing.runs", kind=InstrumentKind.COUNTER, label_keys=("b",))

    prometheus.add(first, 1, {"a": "x"})
    prometheus.add(again, 1, {"b": "y"})

    assert prometheus.registry.get_sample_value("billing_runs_total", {"a": "x"}) == 1


def test_the_fastapi_gateway_serves_the_scrape_when_prometheus_records(prometheus):
    from fastapi.testclient import TestClient

    from sincpro_framework.entrypoints import Exposure
    from sincpro_framework.entrypoints.fastapi import FastApiGateway

    bus = _billing()
    client = TestClient(
        FastApiGateway([bus], exposure=Exposure.CATALOG, unguarded=True).app()
    )
    client.post(
        "/billing/issue-invoice", json={"customer_id": "c", "currency": "USD", "total": "1"}
    )

    scraped = client.get("/metrics")

    assert scraped.status_code == 200
    assert 'billing_issue_invoice_runs_total{currency="USD"} 1.0' in scraped.text


def test_no_scrape_route_when_nothing_is_scraped():
    from fastapi.testclient import TestClient

    from sincpro_framework.entrypoints import Exposure
    from sincpro_framework.entrypoints.fastapi import FastApiGateway

    with metrics.using(InMemoryRecorder()):
        app = FastApiGateway([_billing()], exposure=Exposure.CATALOG, unguarded=True).app()

    assert TestClient(app).get("/metrics").status_code == 404


# --- OpenTelemetry: whatever exporter the process configured ------------------------------------


def test_what_the_bus_measured_reaches_the_otel_meter(otel_reader):
    reader, recorder = otel_reader
    with metrics.using(recorder):
        _billing()(
            CommandIssueInvoice(customer_id="c", currency=Currency.USD, total=Decimal(3)),
            ResponseIssueInvoice,
        )

    (counted,) = _otel_points(reader, "billing.issue_invoice.runs", {"currency": "USD"})
    assert counted.value == 1
    durations = _otel_points(
        reader,
        "sincpro.use_case.duration",
        {
            "sincpro.context": "billing",
            "sincpro.use_case": "CommandIssueInvoice",
            "sincpro.layer": "feature",
            "sincpro.outcome": "ok",
            "error.type": "",
        },
    )
    assert [point.count for point in durations] == [1]


# --- the configuration picks the backend --------------------------------------------------------


@pytest.mark.parametrize(
    ("backend", "chosen"),
    [("off", None), ("prometheus", PrometheusRecorder), ("otel", OtelRecorder)],
)
def test_the_backend_is_the_configurations(monkeypatch, backend, chosen):
    monkeypatch.setattr(active_module.settings, "metrics_backend", backend, raising=False)

    recorder = active_module.from_settings()

    assert recorder is None if chosen is None else isinstance(recorder, chosen)


def test_auto_records_nothing_until_something_is_configured(monkeypatch):
    """Prometheus is never picked by guessing: an unscraped registry is a leak nobody reads."""
    monkeypatch.setattr(active_module.settings, "metrics_backend", "auto", raising=False)
    monkeypatch.setattr(active_module.settings, "otlp_endpoint", None, raising=False)
    monkeypatch.setattr(
        "sincpro_framework.observability.metrics.adapters.otel.host_meter_provider_is_real",
        lambda: False,
    )

    assert active_module.from_settings() is None


def test_auto_records_to_otel_when_an_endpoint_is_configured(monkeypatch):
    monkeypatch.setattr(active_module.settings, "metrics_backend", "auto", raising=False)
    monkeypatch.setattr(active_module.settings, "otlp_endpoint", "http://collector:4317")
    monkeypatch.setattr(
        "sincpro_framework.observability.metrics.adapters.otel.install_otlp_meter_provider",
        lambda endpoint, service: None,
    )

    assert isinstance(active_module.from_settings(), OtelRecorder)


def test_labels_read_by_field_reference_travel_to_every_backend(prometheus):
    """The same declaration, the same series, whichever recorder is behind it."""
    bus = UseFramework("crm", log_after_execution=False)

    @bus.feature(CommandIssueInvoice)
    @metrics.counts(by=(of(CommandIssueInvoice).currency, of(CommandIssueInvoice).channel))
    class Register(Feature):
        def execute(self, dto: CommandIssueInvoice) -> None:
            return None

    bus(CommandIssueInvoice(customer_id="c", currency=Currency.BOB, total=Decimal(1)))

    assert (
        prometheus.registry.get_sample_value(
            "crm_register_runs_total", {"currency": "BOB", "channel": "web"}
        )
        == 1
    )


def test_durations_reach_otel_with_buckets_in_seconds(otel_reader):
    """OTel's default buckets are sized for milliseconds (0, 5, 10, 25 …): a duration in seconds
    lands in the first one and every percentile reads 5 s. Durations carry their own."""
    reader, recorder = otel_reader
    with metrics.using(recorder):
        _billing()(
            CommandIssueInvoice(customer_id="c", currency=Currency.BOB, total=Decimal(1)),
            ResponseIssueInvoice,
        )

    data = reader.get_metrics_data()
    (duration,) = [
        metric
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == "sincpro.use_case.duration"
    ]
    bounds = duration.data.data_points[0].explicit_bounds
    assert bounds[0] == 0.005 and 1.0 in bounds and bounds[-1] == 10.0


def test_a_scrape_says_which_library_and_version_runs_each_context(prometheus):
    _billing()(
        CommandIssueInvoice(customer_id="c", currency=Currency.BOB, total=Decimal(1)),
        ResponseIssueInvoice,
    )

    info = [
        line
        for line in prometheus.exposition().decode().splitlines()
        if line.startswith("sincpro_context_info{")
    ]

    assert (
        len(info) == 1 and 'sincpro_context="billing"' in info[0] and info[0].endswith(" 1.0")
    )
