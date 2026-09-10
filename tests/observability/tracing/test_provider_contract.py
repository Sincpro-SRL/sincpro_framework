"""Contract of ``tracing.setup`` — every promise its docstrings make.

Setup never raises: a missing SDK, a missing endpoint or a broken provider all
degrade to a status the caller reads on ``framework.observability.status``. It also
holds the two design decisions that justify a per-bus provider: each bus keeps its
own ``service.name``, and a TracerProvider the host already registered is never
replaced.

The exporter and the span processor are always faked here — building the real ones
opens a gRPC channel and a background flush thread that outlive the test.
"""

import sys
from unittest.mock import MagicMock

import pytest

import sincpro_framework.observability.tracing.setup as provider_module
from sincpro_framework.observability import ObservabilityIdentity, registry
from sincpro_framework.sincpro_conf import settings

pytest.importorskip("opentelemetry.sdk.trace")

GRPC_EXPORTER = "opentelemetry.exporter.otlp.proto.grpc.trace_exporter"
HTTP_EXPORTER = "opentelemetry.exporter.otlp.proto.http.trace_exporter"
ENDPOINT = "http://collector:4317"


@pytest.fixture(autouse=True)
def clean_registry():
    """Providers live in the registry; a test must not leak one into the next."""
    registry.reset()
    yield
    registry.reset()


def identity_for(bus, artifact="sincpro-payments-sdk", version="5.1.0"):
    return ObservabilityIdentity(artifact=artifact, version=version, bus=bus)


def setup_for(bus, logger=None, **identity_fields) -> dict:
    """Run setup for one bus and return its status as a plain dict to assert on."""
    return provider_module.setup(identity_for(bus, **identity_fields), logger).model_dump()


def provider_of(bus: str):
    """The provider the registry holds for a bus, asserted present."""
    provider = registry.tracer_provider(bus)
    assert provider is not None
    return provider


def fake_export_pipeline(monkeypatch, exporter_module=GRPC_EXPORTER) -> dict:
    """Swap exporter and processor for doubles. Returns what the exporter saw."""
    seen: dict = {}

    def fake_exporter(endpoint=None, **kwargs):
        seen["endpoint"] = endpoint
        return MagicMock(name="exporter")

    monkeypatch.setattr(f"{exporter_module}.OTLPSpanExporter", fake_exporter)
    monkeypatch.setattr(
        "opentelemetry.sdk.trace.export.BatchSpanProcessor",
        lambda exporter: MagicMock(name="processor"),
    )
    return seen


def stub_host_provider(monkeypatch, provider) -> list:
    """Pretend the host registered ``provider``. Returns what sincpro registers."""
    from opentelemetry import trace

    registered: list = []
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: provider)
    monkeypatch.setattr(trace, "set_tracer_provider", registered.append)
    return registered


def real_provider():
    from opentelemetry.sdk.trace import TracerProvider

    return TracerProvider()


def proxy_provider():
    from opentelemetry.trace import ProxyTracerProvider

    return ProxyTracerProvider()


# ---------------------------------------------------------------------------
# Status contract — the documented outcomes
# ---------------------------------------------------------------------------


def test_missing_sdk_turns_tracing_off(monkeypatch):
    """Without opentelemetry-sdk the framework still boots, tracing just stays off."""
    monkeypatch.setitem(sys.modules, "opentelemetry.sdk.trace", None)

    assert setup_for("payment") == {
        "active": False,
        "state": "off",
        "reason": "sdk_missing",
    }


def test_no_endpoint_and_no_host_provider_turns_tracing_off(monkeypatch):
    """Nothing configured anywhere is a legitimate state, not a failure."""
    monkeypatch.setattr(settings, "otlp_endpoint", None)
    stub_host_provider(monkeypatch, proxy_provider())

    assert setup_for("payment") == {
        "active": False,
        "state": "off",
        "reason": "no_endpoint",
    }


def test_host_provider_is_adopted_without_our_own_endpoint(monkeypatch):
    """When Odoo/FastAPI already configured OTel, sincpro rides on it and says so."""
    monkeypatch.setattr(settings, "otlp_endpoint", None)
    stub_host_provider(monkeypatch, real_provider())

    assert setup_for("payment") == {"active": True, "state": "on", "reason": "host"}


def test_no_exporter_installed_is_reported_as_failed(monkeypatch):
    """An endpoint configured with no exporter to reach it is a real misconfiguration."""
    monkeypatch.setattr(settings, "otlp_endpoint", ENDPOINT)
    monkeypatch.setitem(sys.modules, GRPC_EXPORTER, None)
    monkeypatch.setitem(sys.modules, HTTP_EXPORTER, None)

    assert setup_for("payment") == {
        "active": False,
        "state": "failed",
        "reason": "exporter_missing",
    }


def test_setup_never_raises_when_the_provider_explodes(monkeypatch):
    """Any unexpected failure degrades to failed:<reason>; the bus keeps working."""
    import opentelemetry.sdk.trace as sdk_trace

    def boom(**kwargs):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(settings, "otlp_endpoint", ENDPOINT)
    monkeypatch.setattr(sdk_trace, "TracerProvider", boom)

    assert setup_for("payment") == {
        "active": False,
        "state": "failed",
        "reason": "provider exploded",
    }


def test_http_exporter_covers_for_a_missing_grpc_exporter(monkeypatch):
    """Only the http extra installed still exports, to the endpoint from conf."""
    monkeypatch.setattr(settings, "otlp_endpoint", ENDPOINT)
    monkeypatch.setitem(sys.modules, GRPC_EXPORTER, None)
    seen = fake_export_pipeline(monkeypatch, HTTP_EXPORTER)
    stub_host_provider(monkeypatch, proxy_provider())

    status = setup_for("payment")

    assert status == {"active": True, "state": "on", "reason": "init"}
    assert seen["endpoint"] == ENDPOINT


# ---------------------------------------------------------------------------
# Why the provider is per-bus — own service.name, host provider untouched
# ---------------------------------------------------------------------------


def test_resource_service_name_keeps_the_bus_as_last_segment(monkeypatch):
    """Every span this bus exports is labelled artifact:version:bus."""
    monkeypatch.setattr(settings, "otlp_endpoint", ENDPOINT)
    fake_export_pipeline(monkeypatch)
    stub_host_provider(monkeypatch, real_provider())

    setup_for("common_mcp", artifact="sincpro-odoo-mcp", version="0.8.0")

    resource = provider_of("common_mcp").resource
    assert resource.attributes["service.name"] == "sincpro-odoo-mcp:0.8.0:common_mcp"


def test_a_real_host_provider_is_never_replaced(monkeypatch):
    """Registering ours globally would hijack the host's spans. It must not happen."""
    monkeypatch.setattr(settings, "otlp_endpoint", ENDPOINT)
    fake_export_pipeline(monkeypatch)
    host_provider = real_provider()
    registered = stub_host_provider(monkeypatch, host_provider)

    setup_for("payment")

    assert registered == []
    assert registry.tracer_provider("payment") is not host_provider


def test_the_process_takes_the_global_provider_never_a_bus(monkeypatch):
    """Transport instrumentation asks OTel for the global tracer.

    If a bus answered there, every ASGI request and every httpx call would be
    exported as if it belonged to that bounded context.
    """
    from sincpro_framework.observability.registry import PROCESS

    monkeypatch.setattr(settings, "otlp_endpoint", ENDPOINT)
    fake_export_pipeline(monkeypatch)
    registered = stub_host_provider(monkeypatch, proxy_provider())

    setup_for("payment", artifact="sincpro-odoo-mcp", version="0.8.0")

    assert registered == [registry.tracer_provider(PROCESS)]
    assert registry.tracer_provider(PROCESS) is not registry.tracer_provider("payment")


def test_the_process_provider_drops_the_bus_from_its_service_name(monkeypatch):
    """A request belongs to the deployment, not to one of its buses."""
    from sincpro_framework.observability.registry import PROCESS

    monkeypatch.setattr(settings, "otlp_endpoint", ENDPOINT)
    fake_export_pipeline(monkeypatch)
    stub_host_provider(monkeypatch, proxy_provider())

    setup_for("common_mcp", artifact="sincpro-odoo-mcp", version="0.8.0")

    resource = provider_of(PROCESS).resource
    assert resource.attributes["service.name"] == "sincpro-odoo-mcp:0.8.0"


def test_every_bus_shares_one_process_provider(monkeypatch):
    """Three buses must not open three global providers, nor fight over it."""
    from sincpro_framework.observability.registry import PROCESS

    monkeypatch.setattr(settings, "otlp_endpoint", ENDPOINT)
    fake_export_pipeline(monkeypatch)
    registered = stub_host_provider(monkeypatch, proxy_provider())

    for bus in ("common_mcp", "sales_mcp", "mail_mcp"):
        setup_for(bus, artifact="sincpro-odoo-mcp", version="0.8.0")

    assert len(set(id(p) for p in registered)) == 1
    assert registered[0] is registry.tracer_provider(PROCESS)


def test_the_process_provider_is_not_installed_when_the_host_owns_otel(monkeypatch):
    """Inside Odoo there is nothing for us to install: the host got there first."""
    from sincpro_framework.observability.registry import PROCESS

    monkeypatch.setattr(settings, "otlp_endpoint", ENDPOINT)
    fake_export_pipeline(monkeypatch)
    registered = stub_host_provider(monkeypatch, real_provider())

    setup_for("payment")

    assert registered == []
    assert registry.tracer_provider(PROCESS) is None


def test_each_bus_keeps_its_own_provider_and_service_name(monkeypatch):
    """Two buses of one deployment must not collapse into one service in Tempo."""
    monkeypatch.setattr(settings, "otlp_endpoint", ENDPOINT)
    fake_export_pipeline(monkeypatch)
    stub_host_provider(monkeypatch, proxy_provider())

    for bus in ("common_mcp", "sales_mcp"):
        setup_for(bus, artifact="sincpro-odoo-mcp", version="0.8.0")

    common = provider_of("common_mcp")
    sales = provider_of("sales_mcp")
    assert common is not sales
    assert common.resource.attributes["service.name"] == "sincpro-odoo-mcp:0.8.0:common_mcp"
    assert sales.resource.attributes["service.name"] == "sincpro-odoo-mcp:0.8.0:sales_mcp"


def test_setting_up_the_same_bus_twice_reuses_its_provider(monkeypatch):
    """A second build_root_bus() must not stack another exporter on the same bus."""
    monkeypatch.setattr(settings, "otlp_endpoint", ENDPOINT)
    fake_export_pipeline(monkeypatch)
    stub_host_provider(monkeypatch, proxy_provider())

    setup_for("payment")
    first = registry.tracer_provider("payment")
    setup_for("payment")

    assert registry.tracer_provider("payment") is first


# ---------------------------------------------------------------------------
# tracer_for — own provider first, host fallback, never another bus's
# ---------------------------------------------------------------------------


def test_tracer_comes_from_the_bus_provider_when_configured(monkeypatch):
    """Spans must be born in this bus's provider to keep its service.name."""
    provider = MagicMock(name="provider")
    registry.register_tracer_provider("payment", provider)

    assert provider_module.tracer_for("payment") is provider.get_tracer.return_value


def test_tracer_falls_back_to_the_host_provider():
    """Without our own setup, piggyback on whatever provider the host installed."""
    assert provider_module.tracer_for("never-set-up") is not None


def test_a_bus_never_borrows_another_bus_provider(monkeypatch):
    """A span exported under another service's name is worse than no span."""
    other = real_provider()
    registry.register_tracer_provider("payment-qr", other)
    stub_host_provider(monkeypatch, other)

    assert provider_module.tracer_for("bank-account") is None


def test_tracer_is_none_instead_of_raising():
    """A broken provider degrades to no tracing; it never breaks the caller."""
    broken = MagicMock(name="broken_provider")
    broken.get_tracer.side_effect = RuntimeError("no tracer for you")
    registry.register_tracer_provider("payment", broken)

    assert provider_module.tracer_for("payment") is None


def test_logger_starts_emitting_trace_ids_once_otlp_is_on(monkeypatch):
    """Configuring OTLP is what makes logs and spans share a trace_id.

    Counterpart of test_logger_getter_not_registered_without_otlp_endpoint: with an
    endpoint the getter must be installed, otherwise logs and traces stay unlinked.
    """
    from sincpro_log.logger import create_logger

    monkeypatch.setattr(settings, "otlp_endpoint", ENDPOINT)
    fake_export_pipeline(monkeypatch)
    stub_host_provider(monkeypatch, proxy_provider())
    logger = create_logger("with-endpoint-test")

    setup_for("payment", logger=logger)

    assert logger._getter_context is provider_module.current_otel_context


# ---------------------------------------------------------------------------
# End to end — one trace, one identity, even under a host that owns OTel
# ---------------------------------------------------------------------------


def in_memory_pipeline(monkeypatch):
    """Export to memory instead of OTLP, keeping real spans to assert on."""
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    monkeypatch.setattr(
        f"{GRPC_EXPORTER}.OTLPSpanExporter", lambda endpoint=None, **kwargs: exporter
    )
    monkeypatch.setattr(
        "opentelemetry.sdk.trace.export.BatchSpanProcessor", SimpleSpanProcessor
    )
    return exporter


def test_root_span_and_dto_spans_report_the_same_service(monkeypatch):
    """The whole point of a per-bus provider: one trace, one identity.

    The root span used to be created with the *global* tracer, so under Odoo it
    exported as `service.name=odoo` while its own children exported as sincpro —
    one trace split across two services, with the entry span mislabelled.
    """
    from sincpro_framework import DataTransferObject, Feature, UseFramework

    monkeypatch.setattr(settings, "otlp_endpoint", ENDPOINT)
    monkeypatch.setattr(settings, "app_release", "sincpro_mcp_odoo:0.8.0")
    host_provider = real_provider()
    stub_host_provider(monkeypatch, host_provider)
    exporter = in_memory_pipeline(monkeypatch)

    class Ping(DataTransferObject):
        value: str

    framework = UseFramework("common_mcp", log_after_execution=False)

    @framework.feature(Ping)
    class DoPing(Feature):
        def execute(self, dto: Ping) -> str:
            return dto.value

    with framework.with_trace() as traced:
        traced(Ping(value="x"))

    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert set(spans) == {"common_mcp", "Ping"}
    for span in spans.values():
        assert span.resource.attributes["service.name"] == "sincpro_mcp_odoo:0.8.0:common_mcp"
    assert spans["common_mcp"].parent is None
    assert spans["Ping"].parent is not None


# ---------------------------------------------------------------------------
# Sampling — how much of the traffic reaches Tempo
# ---------------------------------------------------------------------------


def sampler_of(bus: str):
    return provider_of(bus).sampler


def test_everything_is_recorded_by_default(monkeypatch):
    """No sampling configured means no traces silently dropped."""
    from opentelemetry.sdk.trace.sampling import ALWAYS_ON

    monkeypatch.setattr(settings, "otlp_endpoint", ENDPOINT)
    monkeypatch.setattr(settings, "otlp_traces_sample_rate", 1.0)
    fake_export_pipeline(monkeypatch)
    stub_host_provider(monkeypatch, proxy_provider())

    setup_for("payment")

    assert sampler_of("payment")._root is ALWAYS_ON


def test_a_ratio_records_only_that_share_of_new_traces(monkeypatch):
    """OTEL_TRACES_SAMPLER_ARG=0.1 must reach the provider as a 10% ratio."""
    from opentelemetry.sdk.trace.sampling import TraceIdRatioBased

    monkeypatch.setattr(settings, "otlp_endpoint", ENDPOINT)
    monkeypatch.setattr(settings, "otlp_traces_sample_rate", 0.1)
    fake_export_pipeline(monkeypatch)
    stub_host_provider(monkeypatch, proxy_provider())

    setup_for("payment")

    root = sampler_of("payment")._root
    assert isinstance(root, TraceIdRatioBased)
    assert root.rate == 0.1


def test_zero_records_nothing_new(monkeypatch):
    from opentelemetry.sdk.trace.sampling import ALWAYS_OFF

    monkeypatch.setattr(settings, "otlp_endpoint", ENDPOINT)
    monkeypatch.setattr(settings, "otlp_traces_sample_rate", 0.0)
    fake_export_pipeline(monkeypatch)
    stub_host_provider(monkeypatch, proxy_provider())

    setup_for("payment")

    assert sampler_of("payment")._root is ALWAYS_OFF


def test_an_upstream_sampling_decision_is_always_honoured(monkeypatch):
    """A request Odoo already decided to sample must not be cut in half by our ratio."""
    from opentelemetry.sdk.trace.sampling import ParentBased

    monkeypatch.setattr(settings, "otlp_endpoint", ENDPOINT)
    monkeypatch.setattr(settings, "otlp_traces_sample_rate", 0.01)
    fake_export_pipeline(monkeypatch)
    stub_host_provider(monkeypatch, proxy_provider())

    setup_for("payment")

    assert isinstance(sampler_of("payment"), ParentBased)


# ---------------------------------------------------------------------------
# Observability never breaks the bus, not even while closing a span
# ---------------------------------------------------------------------------


def exploding_processor():
    """A span processor that fails on close — a host's, or a broken exporter."""
    from opentelemetry.sdk.trace import SpanProcessor

    class ExplodingProcessor(SpanProcessor):
        def on_end(self, span):
            raise RuntimeError("exporter exploded while closing the span")

    return ExplodingProcessor()


def framework_with_exploding_spans(bus: str):
    from sincpro_framework import DataTransferObject, Feature, UseFramework

    provider = real_provider()
    provider.add_span_processor(exploding_processor())
    registry.register_tracer_provider(bus, provider)

    class Charge(DataTransferObject):
        amount: int

    framework = UseFramework(bus, log_after_execution=False)

    @framework.feature(Charge)
    class Do(Feature):
        def execute(self, dto: Charge) -> str:
            if dto.amount < 0:
                raise ValueError("monto invalido")
            return "cobrado"

    framework.build_root_bus()
    return framework, Charge


def test_a_processor_that_fails_on_close_does_not_break_the_execution():
    """Closing the span is observability work; it must not reach the caller."""
    framework, Charge = framework_with_exploding_spans("exploding-close")

    assert framework(Charge(amount=10)) == "cobrado"


def test_a_processor_that_fails_on_close_does_not_swallow_the_real_error():
    """The opposite failure: a broken exporter hiding a business exception."""
    framework, Charge = framework_with_exploding_spans("exploding-close-error")

    with pytest.raises(ValueError, match="monto invalido"):
        framework(Charge(amount=-1))


def test_a_trace_block_survives_a_processor_that_fails_on_close():
    """`with_trace()` ends its root span on exit — same shield applies."""
    framework, Charge = framework_with_exploding_spans("exploding-close-trace")

    with framework.with_trace() as traced:
        assert traced(Charge(amount=5)) == "cobrado"
