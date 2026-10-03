"""The execution context on every signal — PRD_03 §4.10.

    release · service · version · tenant     on the log line, the span, every series, GlitchTip
    every context key                        on the log line, the span and GlitchTip
    a context key declared as a metric label on every series
    of(ContextType)["field"]                 read off the context by the decorators

Each test drives the public doors only: `UseFramework`, `bus.context`, the decorators, an
in-memory recorder, a captured log line, a fake GlitchTip and the in-memory span exporter.
"""

import threading
from enum import StrEnum
from typing import TypedDict

import pytest
from structlog.testing import capture_logs

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework
from sincpro_framework.auth import Identity, as_identity
from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.observability.metrics import InMemoryRecorder, metrics, of
from sincpro_framework.observability.tracing.attributes import traces
from sincpro_framework.sincpro_conf import settings

from .errors.test_sentry import CaptureState, _install_fake_sentry

RELEASE = "registry.example.com/team/billing-svc:2.3.1"
DURATION = "sincpro.use_case.duration"


class Company(StrEnum):
    ACME = "acme-srl"
    BO = "bo-sa"


class BillingContext(TypedDict, total=False):
    tenant: str
    company: Company
    user_id: str
    channel: str


class CommandCharge(DataTransferObject):
    amount: int


class ResponseCharge(DataTransferObject):
    pass


class GatewayDown(Exception):
    pass


def _bus(name: str = "billing", **options) -> UseFramework:  # type: ignore[no-untyped-def]
    framework = UseFramework(name, log_after_execution=False, **options)
    framework.observability._module_name = "not_a_distribution"
    return framework


def _span(otel_setup, name: str):  # type: ignore[no-untyped-def]
    return next(span for span in otel_setup.get_finished_spans() if span.name == name)


@pytest.fixture
def deployed(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "app_release", RELEASE)
    monkeypatch.setattr(settings, "tenant", "deploy-tenant")
    monkeypatch.delenv("OTEL_RESOURCE_ATTRIBUTES", raising=False)


@pytest.fixture
def sentry(monkeypatch) -> CaptureState:  # type: ignore[no-untyped-def]
    state = CaptureState()
    _install_fake_sentry(monkeypatch, state)
    return state


@pytest.fixture
def recorder():  # type: ignore[no-untyped-def]
    recorded = InMemoryRecorder()
    with metrics.using(recorded):
        yield recorded


# --- rule 1: four keys, always, on the four signals -----------------------------------------------


def test_release_service_version_and_tenant_are_on_every_signal(
    deployed, sentry, recorder, otel_setup
):
    framework = _bus()

    @framework.feature(CommandCharge)
    class Charge(Feature):
        def execute(self, dto: CommandCharge) -> None:
            raise GatewayDown("down")

    with capture_logs() as logs, pytest.raises(GatewayDown):
        framework(CommandCharge(amount=1))

    [line] = [entry for entry in logs if entry["log_level"] == "error"]
    [(labels, _)] = recorder.observations(DURATION)
    span = _span(otel_setup, "billing/CommandCharge")

    assert (line["release"], labels["release"], sentry.tags["release"]) == (RELEASE,) * 3
    assert (line["service_name"], labels["service.name"], sentry.tags["service_name"]) == (
        "billing-svc",
    ) * 3
    assert (
        line["service_version"],
        labels["service.version"],
        sentry.tags["service_version"],
    ) == ("2.3.1",) * 3
    assert (
        line["tenant"],
        labels["tenant"],
        span.attributes["tenant"],
        sentry.tags["tenant"],
    ) == ("deploy-tenant",) * 4


def test_a_tenant_declared_only_in_the_resource_attributes_reaches_every_signal(
    monkeypatch, sentry, recorder, otel_setup
):
    """An SDK inside Odoo: the host sets `OTEL_RESOURCE_ATTRIBUTES`, never `TENANT`."""
    monkeypatch.setattr(settings, "tenant", None)
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "tenant=dispel,env=production")
    framework = _bus()

    @framework.feature(CommandCharge)
    class Charge(Feature):
        def execute(self, dto: CommandCharge) -> None:
            raise GatewayDown("down")

    with capture_logs() as logs, pytest.raises(GatewayDown):
        framework(CommandCharge(amount=1))

    [line] = [entry for entry in logs if entry["log_level"] == "error"]
    [(labels, _)] = recorder.observations(DURATION)
    span = _span(otel_setup, "billing/CommandCharge")
    assert (
        line["tenant"],
        labels["tenant"],
        span.attributes["tenant"],
        sentry.tags["tenant"],
        sentry.clients[0]["environment"],
    ) == ("dispel",) * 5


def test_no_tenant_anywhere_is_no_tenant_at_all(monkeypatch, recorder, otel_setup):
    monkeypatch.setattr(settings, "tenant", None)
    monkeypatch.delenv("OTEL_RESOURCE_ATTRIBUTES", raising=False)
    framework = _bus()

    @framework.feature(CommandCharge)
    class Charge(Feature):
        def execute(self, dto: CommandCharge) -> None:
            return None

    with capture_logs() as logs:
        framework(CommandCharge(amount=1))

    [(labels, _)] = recorder.observations(DURATION)
    assert "tenant" not in labels
    assert "tenant" not in _span(otel_setup, "billing/CommandCharge").attributes
    assert all("tenant" not in entry for entry in logs)


# --- rules 2 and 3: the context is the source, the tenant is the execution's -----------------------


def test_one_process_two_executions_each_reports_its_own_tenant(
    deployed, sentry, recorder, otel_setup
):
    framework = _bus()

    @framework.feature(CommandCharge)
    class Charge(Feature):
        def execute(self, dto: CommandCharge) -> None:
            if dto.amount < 0:
                raise GatewayDown("down")

    with capture_logs() as logs:
        with framework.context({"tenant": "acme"}), pytest.raises(GatewayDown):
            framework(CommandCharge(amount=-1))
        acme_tags = dict(sentry.tags)
        with framework.context({"tenant": "bo"}):
            framework(CommandCharge(amount=1))

    spans = [s for s in otel_setup.get_finished_spans() if s.name == "billing/CommandCharge"]
    assert [span.attributes["tenant"] for span in spans] == ["acme", "bo"]
    assert sorted(labels["tenant"] for labels, _ in recorder.observations(DURATION)) == [
        "acme",
        "bo",
    ]
    assert acme_tags["tenant"] == "acme"
    assert {entry["tenant"] for entry in logs if "tenant" in entry} == {"acme", "bo"}
    # The resource describes the process: the deployment's tenant stays there.
    assert all(span.resource.attributes.get("tenant") != "acme" for span in spans)


def test_every_context_key_goes_on_the_span_and_the_event_and_user_id_is_its_user(
    deployed, sentry, otel_setup
):
    framework = _bus()

    @framework.feature(CommandCharge)
    class Charge(Feature):
        def execute(self, dto: CommandCharge) -> None:
            raise GatewayDown("down")

    with framework.context({"company": "acme-srl", "user_id": "user:42", "datos_x": 7}):
        with pytest.raises(GatewayDown):
            framework(CommandCharge(amount=1))

    span = _span(otel_setup, "billing/CommandCharge")
    assert (span.attributes["company"], span.attributes["user_id"]) == ("acme-srl", "user:42")
    assert span.attributes["datos_x"] == 7
    assert (sentry.tags["company"], sentry.tags["user_id"], sentry.tags["datos_x"]) == (
        "acme-srl",
        "user:42",
        "7",
    )
    assert sentry.user == {"id": "user:42"}


def test_a_key_hidden_in_logs_is_on_no_signal(deployed, sentry, recorder, otel_setup):
    framework = _bus(hide_in_logs=["token"], metric_labels=["token"])

    @framework.feature(CommandCharge)
    class Charge(Feature):
        def execute(self, dto: CommandCharge) -> None:
            raise GatewayDown("down")

    with capture_logs() as logs, framework.context({"token": "s3cr3t"}):
        with pytest.raises(GatewayDown):
            framework(CommandCharge(amount=1))

    [(labels, _)] = recorder.observations(DURATION)
    assert "token" not in _span(otel_setup, "billing/CommandCharge").attributes
    assert "token" not in sentry.tags
    assert "token" not in labels
    assert all("token" not in entry for entry in logs)


def test_the_authenticated_identity_gives_the_tenant_and_the_user_when_the_context_has_none(
    deployed, sentry, recorder, otel_setup
):
    framework = _bus()

    @framework.feature(CommandCharge)
    class Charge(Feature):
        def execute(self, dto: CommandCharge) -> None:
            raise GatewayDown("down")

    with as_identity(Identity.user("user:42", tenant="bo")), pytest.raises(GatewayDown):
        framework(CommandCharge(amount=1))
    with as_identity(Identity.user("user:7", tenant="bo")), framework.context(
        {"tenant": "acme"}
    ):
        with pytest.raises(GatewayDown):
            framework(CommandCharge(amount=2))

    first, second = [
        s for s in otel_setup.get_finished_spans() if s.name == "billing/CommandCharge"
    ]
    assert (first.attributes["tenant"], first.attributes["user_id"]) == ("bo", "user:42")
    # The context is the framework's language: what it says wins over the identity's.
    assert second.attributes["tenant"] == "acme"
    assert sentry.user == {"id": "user:7"}


# --- rule 4: a value counts from the moment it is set ---------------------------------------------


def test_a_key_set_midway_by_the_handler_or_an_interceptor_counts(
    deployed, recorder, otel_setup
):
    framework = _bus(metric_labels=["company"])

    @framework.interceptor(CommandCharge)
    def tenant_from_the_command(dto, call_next):  # type: ignore[no-untyped-def]
        with framework.context({"tenant": f"t-{dto.amount}"}):
            return call_next(dto)

    @framework.feature(CommandCharge)
    class Charge(Feature):
        def execute(self, dto: CommandCharge) -> None:
            self.context["company"] = "acme-srl"

    framework(CommandCharge(amount=5))

    span = _span(otel_setup, "billing/CommandCharge")
    [(labels, _)] = recorder.observations(DURATION)
    assert (span.attributes["tenant"], span.attributes["company"]) == ("t-5", "acme-srl")
    assert (labels["tenant"], labels["company"]) == ("t-5", "acme-srl")


def test_a_nested_bus_and_a_thread_inherit_the_tenant(deployed, otel_setup):
    payments = _bus("payments")
    billing = _bus()

    class CommandPay(DataTransferObject):
        pass

    class CommandBill(DataTransferObject):
        pass

    @payments.feature(CommandPay)
    class Pay(Feature):
        def execute(self, dto: CommandPay) -> None:
            return None

    @billing.feature(CommandBill)
    class Bill(Feature):
        def execute(self, dto: CommandBill) -> None:
            return None

    @billing.app_service(CommandCharge)
    class Charge(ApplicationService):
        def execute(self, dto: CommandCharge) -> None:
            payments(CommandPay())
            worker = threading.Thread(
                target=self.feature_bus.thread_context().execute, args=(CommandBill(),)
            )
            worker.start()
            worker.join()

    with billing.context({"tenant": "acme"}):
        billing(CommandCharge(amount=1))

    assert _span(otel_setup, "payments/CommandPay").attributes["tenant"] == "acme"
    assert _span(otel_setup, "billing/CommandBill").attributes["tenant"] == "acme"


# --- rule 5: metric labels declared on the bus ------------------------------------------------------


def test_a_declared_metric_label_is_on_every_series_of_the_bus(deployed, recorder):
    framework = _bus(metric_labels=(of(BillingContext)["company"],))

    @framework.feature(CommandCharge)
    @metrics.counts()
    class Charge(Feature):
        visits = metrics.counter()

        def execute(self, dto: CommandCharge) -> None:
            self.visits.add(1)

    with framework.context({"company": Company.ACME}):
        framework(CommandCharge(amount=1))
    framework(CommandCharge(amount=2))

    [(with_company, _), (without, _)] = recorder.observations(DURATION)
    assert with_company["company"] == "acme-srl" and "company" not in without
    assert {labels for labels in recorder.totals("billing.charge.runs")} == {
        (
            ("company", "acme-srl"),
            ("release", RELEASE),
            ("service.name", "billing-svc"),
            ("service.version", "2.3.1"),
            ("tenant", "deploy-tenant"),
        ),
        (
            ("release", RELEASE),
            ("service.name", "billing-svc"),
            ("service.version", "2.3.1"),
            ("tenant", "deploy-tenant"),
        ),
    }
    assert sum(recorder.totals("billing.charge.visits").values()) == 2


def test_a_metric_label_in_the_settings_is_on_every_series(monkeypatch, deployed, recorder):
    monkeypatch.setattr(settings, "metric_labels", ["channel"], raising=False)
    framework = _bus()

    @framework.feature(CommandCharge)
    class Charge(Feature):
        def execute(self, dto: CommandCharge) -> None:
            return None

    with framework.context({"channel": "pos"}):
        framework(CommandCharge(amount=1))

    [(labels, _)] = recorder.observations(DURATION)
    assert labels["channel"] == "pos"


def test_the_correlation_labels_reach_prometheus(deployed):
    pytest.importorskip("prometheus_client")
    from prometheus_client import CollectorRegistry

    from sincpro_framework.observability.metrics.adapters.prometheus import PrometheusRecorder

    scraped = PrometheusRecorder(CollectorRegistry())
    framework = _bus(metric_labels=["company"])

    @framework.feature(CommandCharge)
    @metrics.counts()
    class Charge(Feature):
        def execute(self, dto: CommandCharge) -> None:
            return None

    with metrics.using(scraped), framework.context({"company": "acme-srl"}):
        framework(CommandCharge(amount=1))

    assert (
        scraped.registry.get_sample_value(
            "billing_charge_runs_total",
            {
                "company": "acme-srl",
                "release": RELEASE,
                "service_name": "billing-svc",
                "service_version": "2.3.1",
                "tenant": "deploy-tenant",
            },
        )
        == 1
    )


# --- rule 6: of(ContextType)["field"] ---------------------------------------------------------------


def test_the_decorators_read_the_typed_context(deployed, recorder, otel_setup):
    framework = _bus()

    class BillingFeature(Feature[CommandCharge, ResponseCharge, BillingContext]):
        pass

    @framework.feature(CommandCharge)
    @metrics.counts(by=of(BillingContext)["company"])
    @traces.attributes(of(BillingContext)["user_id"], namespace="billing")
    class Charge(BillingFeature):
        def execute(self, dto: CommandCharge) -> ResponseCharge:
            return ResponseCharge()

    with framework.context({"company": Company.BO, "user_id": "user:9"}):
        framework(CommandCharge(amount=1))

    span = _span(otel_setup, "billing/CommandCharge")
    assert span.attributes["billing.user_id"] == "user:9"
    [labels] = recorder.totals("billing.charge.runs")
    assert dict(labels)["company"] == "bo-sa"


def test_a_context_field_that_does_not_exist_or_another_context_is_refused_at_import():
    with pytest.raises(ContractViolation, match="has no field 'compny'"):
        of(BillingContext)["compny"]

    class OtherContext(TypedDict, total=False):
        region: str

    class BillingFeature(Feature[CommandCharge, ResponseCharge, BillingContext]):
        pass

    with pytest.raises(ContractViolation, match="not the context"):

        @metrics.counts(by=of(OtherContext)["region"])
        class Charge(BillingFeature):
            def execute(self, dto: CommandCharge) -> ResponseCharge:
                return ResponseCharge()


# --- queues hand the message's correlation to the context -------------------------------------------


def test_a_queue_message_hands_its_correlation_and_tenant_to_the_context():
    pytest.importorskip("faststream")
    from sincpro_framework.entrypoints.faststream.envelope import Envelope
    from sincpro_framework.entrypoints.faststream.wire import queue_context

    envelope = Envelope(correlationid="corr-1")

    assert queue_context(envelope, {"tenant": "acme"}) == {
        "correlation_id": "corr-1",
        "tenant_id": "acme",
        "tenant": "acme",  # what this wire wrote before tenant_id was the standard key
    }
    assert queue_context(Envelope(), {}) == {}
