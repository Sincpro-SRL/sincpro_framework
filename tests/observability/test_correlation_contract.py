"""One failure, four signals, the same keys — what makes a dashboard's variables filter every
panel, and a `trace_id` open the span, its log lines and its GlitchTip issue.

    service     log `service_name` · resource `service.name` · metric `service.name` · tag
    version     log `service_version` · resource `service.version` · tag `sincpro.version`
    tenant      log `tenant` · resource `tenant` · tag `tenant`
    context     log `sincpro_context` · span `sincpro.context` · metric · tag
    use case    log `sincpro_use_case` · span `sincpro.use_case` · metric · tag
    outcome     log `sincpro_outcome` · span `sincpro.outcome` · metric · tag
    error       log `error_type` · span `error.type` · metric · tag
    trace id    log `trace_id` · the span · tag `trace_id` + the event's trace context
"""

import pytest
from structlog.testing import capture_logs

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.observability.domain import installed_version, resolve_identity
from sincpro_framework.observability.metrics import InMemoryRecorder, metrics
from sincpro_framework.sincpro_conf import settings

from .errors.test_sentry import CaptureState, _install_fake_sentry

RELEASE = "registry.example.com/team/billing-svc:2.3.1"


class CommandCharge(DataTransferObject):
    amount: int


class GatewayDown(Exception):
    pass


def _billing() -> UseFramework:
    framework = UseFramework("billing", log_after_execution=False)
    # The test module is not an installed distribution: APP_RELEASE names the service.
    framework.observability._module_name = "not_a_distribution"

    @framework.feature(CommandCharge)
    class Charge(Feature):
        def execute(self, dto: CommandCharge) -> None:
            raise GatewayDown("the gateway did not answer")

    return framework


def test_one_failure_carries_the_same_keys_on_every_signal(monkeypatch, otel_setup):
    monkeypatch.setattr(settings, "app_release", RELEASE)
    monkeypatch.setattr(settings, "tenant", "acme")
    sentry = CaptureState()
    _install_fake_sentry(monkeypatch, sentry)
    recorder = InMemoryRecorder()
    framework = _billing()

    with metrics.using(recorder), capture_logs() as logs:
        with pytest.raises(GatewayDown):
            framework(CommandCharge(amount=10))

    [span] = otel_setup.get_finished_spans()
    trace_id = format(span.get_span_context().trace_id, "032x")
    [line] = [entry for entry in logs if entry["log_level"] == "error"]
    [(labels, _)] = recorder.observations("sincpro.use_case.duration")
    tags = sentry.tags

    assert span.name == "billing/CommandCharge"
    assert (line["service_name"], labels["service.name"], tags["service_name"]) == (
        "billing-svc",
        "billing-svc",
        "billing-svc",
    )
    assert (line["service_version"], tags["sincpro.version"]) == ("2.3.1", "2.3.1")
    assert (line["tenant"], tags["tenant"]) == ("acme", "acme")
    assert (
        line["sincpro_context"],
        span.attributes["sincpro.context"],
        labels["sincpro.context"],
        tags["sincpro.context"],
    ) == ("billing",) * 4
    assert (
        line["sincpro_use_case"],
        span.attributes["sincpro.use_case"],
        labels["sincpro.use_case"],
        tags["sincpro.use_case"],
    ) == ("billing.CommandCharge",) * 4
    assert (
        line["sincpro_outcome"],
        span.attributes["sincpro.outcome"],
        labels["sincpro.outcome"],
        tags["sincpro.outcome"],
    ) == ("internal",) * 4
    assert (
        line["error_type"],
        span.attributes["error.type"],
        labels["error.type"],
        tags["error.type"],
    ) == ("GatewayDown",) * 4
    assert (line["trace_id"], tags["trace_id"], sentry.contexts["trace"]["trace_id"]) == (
        trace_id,
    ) * 3
    # Unchanged, the alerts read them: the release as deployed, the context's own tag.
    assert sentry.release == RELEASE
    assert tags["sincpro.instance"] == "billing"
    assert line["app_name"] == "billing"


def test_a_successful_run_is_ok_on_its_span_and_its_lines_name_where_they_ran(
    monkeypatch, otel_setup
):
    monkeypatch.setattr(settings, "app_release", RELEASE)
    framework = UseFramework("billing", log_after_execution=True)
    framework.observability._module_name = "not_a_distribution"

    @framework.feature(CommandCharge)
    class Charge(Feature):
        def execute(self, dto: CommandCharge) -> None:
            return None

    with capture_logs() as logs:
        framework(CommandCharge(amount=1))

    [span] = otel_setup.get_finished_spans()
    executing = next(entry for entry in logs if "Executing feature" in entry["event"])
    assert span.attributes["sincpro.outcome"] == "ok"
    assert executing["sincpro_context"] == "billing"
    assert executing["sincpro_use_case"] == "billing.CommandCharge"
    assert executing["service_name"] == "billing-svc"
    assert executing["trace_id"] == format(span.get_span_context().trace_id, "032x")


def test_an_sdk_inside_odoo_keeps_its_own_name_and_version(monkeypatch):
    """Odoo's `APP_RELEASE` names the host; the SDK that built the bus is still the SDK — the
    one that failed, in the version that failed."""
    monkeypatch.setattr(
        settings, "app_release", "registry.digitalocean.com/sincpro/odoo:18.4.0"
    )
    monkeypatch.setattr(settings, "otel_service_name", "odoo")

    identity = resolve_identity("siat-soap-sdk", module_name="sincpro_log")

    assert identity.service_name == "sincpro-log"
    assert identity.service_version == installed_version("sincpro-log")
    assert identity.bus == "siat-soap-sdk"
