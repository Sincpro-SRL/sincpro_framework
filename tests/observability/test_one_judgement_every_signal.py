"""Whether a failure is a problem of the service is decided once (`Observability.faulted`, the rule
of `common.failures.is_fault`) and every signal reads the same answer, so none contradicts
another: GlitchTip, the log line, the span's status, the metrics and the `ExecutionFailed` event.

    fault       reported · `error` with its traceback · span ERROR · sincpro.fault="true" · fault=True
    no fault    —        · `info`, no traceback       · span unset · sincpro.fault="false" · fault=False

`sincpro.outcome` keeps saying what happened — `not_found`, `conflict`, `unavailable` — either way.
"""

from typing import Any

import pytest
from structlog.testing import capture_logs

from sincpro_framework import DataTransferObject, Feature, UseFramework, exceptions
from sincpro_framework.bus_pipeline.outcomes.events import ExecutionFailed
from sincpro_framework.ddd import exceptions as ddd_exceptions
from sincpro_framework.observability.metrics import InMemoryRecorder, metrics
from sincpro_framework.observability.metrics.infrastructure.execution import USE_CASE_DURATION

from .errors.test_sentry import CaptureState, _install_fake_sentry


class CommandIssue(DataTransferObject):
    number: str = "F-1"


class InvoiceNotFound(exceptions.ClientError):
    failure_kind = exceptions.FailureKind.NOT_FOUND


class InvoiceLocked(exceptions.ClientError):
    failure_kind = exceptions.FailureKind.CONFLICT


class NotEnoughCredit(ddd_exceptions.DomainError):
    pass


class SiatDown(exceptions.ServiceUnavailableError):
    pass


class SiatLost(exceptions.OutcomeUnknownError):
    pass


class LibraryTimeout(Exception):
    pass


CASES = [
    pytest.param(InvoiceNotFound("F-9"), (), (), "not_found", False, id="not found"),
    pytest.param(InvoiceLocked("F-9"), (), (), "conflict", False, id="conflict"),
    pytest.param(NotEnoughCredit("no credit"), (), (), "domain", False, id="a rule said no"),
    pytest.param(
        exceptions.ProgrammingError("wrong call"), (), (), "internal", True, id="the code's"
    ),
    pytest.param(SiatDown("down"), (), (), "unavailable", True, id="a service down"),
    pytest.param(
        SiatLost("no answer"), (), (), "unknown_outcome", True, id="outcome unknown"
    ),
    pytest.param(KeyError("total"), (), (), "internal", True, id="a crash"),
    pytest.param(
        LibraryTimeout("slow"),
        (LibraryTimeout,),
        (),
        "internal",
        False,
        id="ignored by the bus",
    ),
    pytest.param(
        NotEnoughCredit("fraud?"), (), (NotEnoughCredit,), "domain", True, id="reported by it"
    ),
]


def _failing(error: Exception, ignored: tuple, reported: tuple) -> UseFramework:
    bus = UseFramework(f"judged-{type(error).__name__}", log_after_execution=False)
    bus.ignore_sentry_exceptions(*ignored)
    bus.report_sentry_exceptions(*reported)

    @bus.feature(CommandIssue)
    class Issue(Feature):
        def execute(self, dto: CommandIssue) -> None:
            raise error

    return bus


@pytest.mark.parametrize(("error", "ignored", "reported", "kind", "fault"), CASES)
def test_every_signal_says_the_same_of_a_failure(
    monkeypatch, otel_setup, error, ignored, reported, kind, fault
):
    sentry = CaptureState()
    _install_fake_sentry(monkeypatch, sentry)
    bus = _failing(error, ignored, reported)
    failed: list[ExecutionFailed] = []
    bus.on_failure(failed.append)
    recorder = InMemoryRecorder()

    with metrics.using(recorder), capture_logs() as logs, pytest.raises(type(error)):
        bus(CommandIssue())

    ((labels, _),) = recorder.observations(USE_CASE_DURATION.name)
    [line] = [one for one in logs if "failed" in one["event"]]
    [span] = [
        one for one in otel_setup.get_finished_spans() if one.name.endswith("CommandIssue")
    ]
    [event] = failed

    said: dict[str, Any] = {
        "sentry": len(sentry.errors) == 1,
        "log": (line["log_level"], "exc_info" in line),
        "span": span.status.status_code.name,
        "metric": (labels["sincpro.outcome"], labels["sincpro.fault"]),
        "event": (event.kind, event.fault),
        "span attributes": (
            span.attributes["sincpro.outcome"],
            span.attributes["sincpro.fault"],
        ),
    }
    assert said == {
        "sentry": fault,
        "log": ("error", True) if fault else ("info", False),
        "span": "ERROR" if fault else "UNSET",
        "metric": (kind, "true" if fault else "false"),
        "event": (kind, fault),
        "span attributes": (kind, "true" if fault else "false"),
    }


def test_a_listener_that_pages_somebody_hears_only_the_faults(otel_setup):
    from sincpro_framework.ddd.criteria import Condition

    paged: list[str] = []
    bus = UseFramework("judged-pager", log_after_execution=False)

    class CommandGet(DataTransferObject):
        number: str

    class CommandSend(DataTransferObject):
        number: str

    @bus.feature(CommandGet)
    class Get(Feature):
        def execute(self, dto: CommandGet) -> None:
            raise InvoiceNotFound(dto.number)

    @bus.feature(CommandSend)
    class Send(Feature):
        def execute(self, dto: CommandSend) -> None:
            raise SiatDown("SIAT did not answer")

    bus.on_failure(
        lambda failure: paged.append(failure.error_type),
        when=Condition(field="fault", value=True),
    )
    for dto in (CommandGet(number="F-404"), CommandSend(number="F-1")):
        with pytest.raises(Exception):
            bus(dto)

    assert paged == ["SiatDown"]
