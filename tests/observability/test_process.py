"""The transport door: what a service needs on top of its buses.

A library inside Odoo never touches any of this — there is no transport of ours to
instrument. A service does: the ASGI request, the httpx call, the access log, a 500
that dies before any Feature runs. All of it belongs to the process, and none of it
may be exported under a bus's name.
"""

import pytest

from sincpro_framework.observability import process, registry
from sincpro_framework.observability.registry import PROCESS
from sincpro_framework.sincpro_conf import settings

pytest.importorskip("opentelemetry.sdk.trace")

ENDPOINT = "http://collector:4317"


@pytest.fixture(autouse=True)
def clean_registry():
    registry.reset()
    yield
    registry.reset()


def in_memory_pipeline(monkeypatch):
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    monkeypatch.setattr(
        "opentelemetry.exporter.otlp.proto.grpc.trace_exporter.OTLPSpanExporter",
        lambda endpoint=None, **kwargs: exporter,
    )
    monkeypatch.setattr(
        "opentelemetry.sdk.trace.export.BatchSpanProcessor", SimpleSpanProcessor
    )
    return exporter


def stub_proxy_global(monkeypatch):
    """Nobody owns OTel yet — the situation of our own services."""
    from opentelemetry import trace
    from opentelemetry.trace import ProxyTracerProvider

    installed: list = []
    monkeypatch.setattr(
        trace,
        "get_tracer_provider",
        lambda: installed[-1] if installed else ProxyTracerProvider(),
    )
    monkeypatch.setattr(trace, "set_tracer_provider", installed.append)
    return installed


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def test_the_process_identity_has_no_bus_segment(monkeypatch):
    monkeypatch.setattr(settings, "app_release", "sincpro-odoo-mcp:0.8.0")
    monkeypatch.setattr(settings, "otel_service_name", None)

    assert process.identity.service_name == "sincpro-odoo-mcp:0.8.0"


def test_the_process_reuses_the_identity_a_bus_announced(monkeypatch):
    """Transport and buses of one deployment must not disagree on the version."""
    from sincpro_framework.observability.domain import ObservabilityIdentity

    announced = ObservabilityIdentity(artifact="sincpro-odoo-mcp", version="0.8.0", bus="")
    registry.register_process_identity(announced)
    monkeypatch.setattr(settings, "app_release", "otra-cosa:9.9.9")

    assert process.identity is announced


def test_the_process_release_is_what_glitchtip_gets(monkeypatch):
    monkeypatch.setattr(settings, "app_release", "sincpro-odoo-mcp:0.8.0")

    assert process.identity.release == "sincpro-odoo-mcp:0.8.0"


# ---------------------------------------------------------------------------
# The three things the transport asks for
# ---------------------------------------------------------------------------


def test_the_tracer_is_the_global_one(monkeypatch):
    """That is what OpenTelemetryMiddleware and HTTPXClientInstrumentor use."""
    assert process.tracer("opentelemetry.instrumentation.asgi") is not None


def test_the_tracer_is_none_instead_of_raising_without_otel(monkeypatch):
    """A caller can skip instrumenting without guarding the import itself."""
    import sys

    monkeypatch.setitem(sys.modules, "opentelemetry", None)

    assert process.tracer("asgi") is None


def test_trace_ids_are_empty_outside_a_span():
    assert process.trace_ids() == {}


def test_trace_ids_follow_the_active_span(otel_setup):
    """An access log line lands on the trace of the request that caused it."""
    from opentelemetry import trace

    tracer = trace.get_tracer("test")
    with tracer.start_as_current_span("POST /mcp") as span:
        ids = process.trace_ids()

    span_ctx = span.get_span_context()
    assert ids["trace_id"] == format(span_ctx.trace_id, "032x")
    assert ids["span_id"] == format(span_ctx.span_id, "016x")


def test_a_process_logger_stamps_the_ids_of_the_active_request(otel_setup):
    """The whole point: an access log line carries the same ids as the bus's logs."""
    from opentelemetry import trace
    from sincpro_log.logger import create_logger

    logger = create_logger("process-log-test")
    logger.set_getter_context(process.trace_ids)

    tracer = trace.get_tracer("test")
    with tracer.start_as_current_span("POST /mcp") as span:
        fields = dict(logger.logger_fields)

    assert fields["trace_id"] == format(span.get_span_context().trace_id, "032x")
    assert "trace_id" not in logger.logger_fields


def test_recording_a_transport_error_never_raises():
    """A 500 in Starlette must not become a second 500 inside observability."""
    process.record_error(RuntimeError("500 en el middleware"), layer="asgi")


# ---------------------------------------------------------------------------
# End to end: one trace, two identities
# ---------------------------------------------------------------------------


def test_a_request_and_its_dto_report_different_services_in_one_trace(monkeypatch):
    """The separation this exists for.

    The request is the deployment's; the DTO is the bounded context's. Same
    trace_id, because OTel propagates through contextvars — not because they share
    a Resource.
    """
    from sincpro_framework import DataTransferObject, Feature, UseFramework

    monkeypatch.setattr(settings, "otlp_endpoint", ENDPOINT)
    monkeypatch.setattr(settings, "app_release", "sincpro-odoo-mcp:0.8.0")
    exporter = in_memory_pipeline(monkeypatch)
    stub_proxy_global(monkeypatch)  # nobody owns OTel yet, as in our own services

    class Quote(DataTransferObject):
        amount: int

    framework = UseFramework("sales_mcp", log_after_execution=False)
    framework.observability._module_name = "not_a_distribution"

    @framework.feature(Quote)
    class DoQuote(Feature):
        def execute(self, dto: Quote) -> str:
            return "quoted"

    framework.build_root_bus()

    transport = process.tracer("opentelemetry.instrumentation.asgi")
    assert transport is not None
    with transport.start_as_current_span("POST /mcp"):
        with framework.with_parent_trace() as bus:
            bus(Quote(amount=1))

    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert set(spans) == {"POST /mcp", "Quote"}
    assert spans["POST /mcp"].resource.attributes["service.name"] == "sincpro-odoo-mcp:0.8.0"
    assert spans["Quote"].resource.attributes["service.name"] == (
        "sincpro-odoo-mcp:0.8.0:sales_mcp"
    )
    quote_context = spans["Quote"].get_span_context()
    request_context = spans["POST /mcp"].get_span_context()
    assert quote_context is not None and request_context is not None
    assert quote_context.trace_id == request_context.trace_id
    assert registry.tracer_provider(PROCESS) is not registry.tracer_provider("sales_mcp")


# ---------------------------------------------------------------------------
# Status — what the boot log should say
# ---------------------------------------------------------------------------


def test_status_says_installed_when_the_framework_owns_the_global(monkeypatch):
    monkeypatch.setattr(settings, "otlp_endpoint", ENDPOINT)
    in_memory_pipeline(monkeypatch)
    stub_proxy_global(monkeypatch)
    from sincpro_framework.observability.domain import ObservabilityIdentity
    from sincpro_framework.observability.tracing.setup import install_process_provider

    install_process_provider(ObservabilityIdentity(artifact="svc", version="1"), ENDPOINT)

    assert process.status.model_dump() == {
        "active": True,
        "state": "on",
        "reason": "installed",
    }


def test_status_says_host_when_someone_got_there_first(monkeypatch):
    """Inside Odoo the transport is already instrumented by the host."""
    from sincpro_framework.observability.tracing import setup as setup_module

    monkeypatch.setattr(setup_module, "host_provider_is_real", lambda: True)
    monkeypatch.setattr(
        "sincpro_framework.observability.api.host_provider_is_real", lambda: True
    )

    assert process.status.model_dump() == {"active": True, "state": "on", "reason": "host"}


def test_status_says_off_when_nothing_is_registered(monkeypatch):
    """Transport spans would be no-ops, and the boot log should say so."""
    monkeypatch.setattr(
        "sincpro_framework.observability.api.host_provider_is_real", lambda: False
    )

    assert process.status.active is False


# ---------------------------------------------------------------------------
# GlitchTip — a transport failure under the process release
# ---------------------------------------------------------------------------


def install_fake_sentry(monkeypatch) -> dict:
    """Capture what would reach GlitchTip, without a network or a real client."""
    import sys

    from sincpro_framework.observability.errors import setup as errors_setup

    captured: dict = {"errors": [], "tags": {}, "releases": []}

    class FakeScope:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def set_client(self, client):
            captured["releases"].append(client.release)

        def set_tag(self, key, value):
            captured["tags"][key] = value

    class FakeClient:
        def __init__(self, **kwargs):
            self.release = kwargs.get("release", "")

    class FakeSentry:
        Client = FakeClient

        @staticmethod
        def isolation_scope():
            return FakeScope()

        @staticmethod
        def capture_exception(error):
            captured["errors"].append(error)

    monkeypatch.setattr(errors_setup, "SDK_AVAILABLE", True)
    monkeypatch.setattr(errors_setup.settings, "sentry_dsn", "https://key@glitchtip/1")
    monkeypatch.setitem(sys.modules, "sentry_sdk", FakeSentry)
    return captured


def test_a_transport_error_is_reported_under_the_process_release(monkeypatch):
    """A 500 in Starlette belongs to the deployment, not to any bounded context."""
    monkeypatch.setattr(settings, "app_release", "sincpro-odoo-mcp:0.8.0")
    captured = install_fake_sentry(monkeypatch)

    error = RuntimeError("500 en el middleware")
    process.record_error(error, layer="asgi")

    assert captured["errors"] == [error]
    assert captured["releases"] == ["sincpro-odoo-mcp:0.8.0"]
    assert captured["tags"]["sincpro.layer"] == "asgi"
    assert "sincpro.instance" not in captured["tags"]  # no bus owns a transport error


# ---------------------------------------------------------------------------
# The boot a service actually writes
# ---------------------------------------------------------------------------


def test_the_whole_service_boot(monkeypatch, capsys):
    """What the MCP's `main()` does, end to end, with nothing private imported.

    Build the buses, bind the process logger, instrument the transport, serve a
    request. One trace_id across HTTP, the DTO and the process log line.
    """
    from sincpro_log.logger import create_logger

    from sincpro_framework import DataTransferObject, Feature, UseFramework

    monkeypatch.setattr(settings, "otlp_endpoint", ENDPOINT)
    monkeypatch.setattr(settings, "app_release", "sincpro-odoo-mcp:0.8.0")
    exporter = in_memory_pipeline(monkeypatch)
    stub_proxy_global(monkeypatch)

    class ListTools(DataTransferObject):
        token: str

    buses = []
    for name in ("common_mcp", "sales_mcp"):
        framework = UseFramework(name, log_after_execution=False)
        framework.observability._module_name = "not_a_distribution"

        @framework.feature(ListTools)
        class Do(Feature):
            def execute(self, dto: ListTools) -> str:
                return "ok"

        framework.build_root_bus()
        buses.append(framework)

    access_log = create_logger("mcp-access-log")
    process.bind_logger(access_log)

    assert process.status.reason == "installed"

    transport = process.tracer("opentelemetry.instrumentation.asgi")
    assert transport is not None
    with transport.start_as_current_span("POST /mcp"):
        access_log_fields = dict(access_log.logger_fields)
        with buses[1].with_parent_trace() as bus:
            bus(ListTools(token="t"))

    spans = {span.name: span for span in exporter.get_finished_spans()}
    services = {
        name: span.resource.attributes["service.name"] for name, span in spans.items()
    }
    assert services == {
        "POST /mcp": "sincpro-odoo-mcp:0.8.0",
        "ListTools": "sincpro-odoo-mcp:0.8.0:sales_mcp",
    }

    request_context = spans["POST /mcp"].get_span_context()
    dto_context = spans["ListTools"].get_span_context()
    assert request_context is not None and dto_context is not None
    assert dto_context.trace_id == request_context.trace_id
    assert access_log_fields["trace_id"] == format(request_context.trace_id, "032x")
