"""What a call did goes out as events: every use case that answered, with its DTO and response; the
failure that escaped, with its exception — once, where the caller receives it — and never when an
error handler answered instead."""

from typing import Any

import pytest

from sincpro_framework import (
    ApplicationService,
    DataTransferObject,
    FailureKind,
    Feature,
    UseFramework,
)
from sincpro_framework.bus_pipeline.outcomes.events import (
    ExecutionCompleted,
    ExecutionFailed,
)
from sincpro_framework.bus_pipeline.outcomes.listeners import (
    completions,
    failures,
    to_publisher,
)
from sincpro_framework.context.infrastructure.tree import current_execution
from sincpro_framework.event_driven import Publisher, Subscriber, SyncQueue


class CommandIssueInvoice(DataTransferObject):
    order_id: str = "O-1"


class CommandConfirmSale(DataTransferObject):
    pass


class SiatUnavailable(Exception):
    pass


@pytest.fixture(autouse=True)
def nobody_listens_after():
    yield
    failures.clear()
    completions.clear()


def _billing(name: str, executions: list[Any] | None = None) -> UseFramework:
    billing = UseFramework(name, log_after_execution=False)

    @billing.feature(CommandIssueInvoice)
    class IssueInvoice(Feature):
        def execute(self, dto: CommandIssueInvoice) -> None:
            if executions is not None:
                executions.append(current_execution())
            raise SiatUnavailable("SIAT is not answering")

    return billing


def test_an_escaped_failure_goes_out_with_its_exception():
    heard: list[ExecutionFailed] = []
    executions: list[Any] = []
    billing = _billing("failures-escaped", executions)
    billing.on_failure(heard.append)

    with billing.context({"tenant_id": "acme", "correlation_id": "flow-1"}):
        with pytest.raises(SiatUnavailable) as raised:
            billing(CommandIssueInvoice(order_id="O-7"))

    [failure] = heard
    assert (failure.use_case, failure.bus, failure.level) == (
        "failures-escaped.CommandIssueInvoice",
        "failures-escaped",
        "feature",
    )
    assert (failure.error_type, failure.error) == ("SiatUnavailable", "SIAT is not answering")
    assert (failure.kind, failure.retry_after) == ("internal", None)
    assert failure.dto == {"order_id": "O-7"}
    assert failure.execution_id == executions[0].execution_id
    assert failure.causation_id == executions[0].execution_id
    assert failure.correlation_id == "flow-1"
    assert failure.context["tenant_id"] == "acme"
    assert raised.value.failure_id == failure.id  # type: ignore[attr-defined]


def test_a_failure_says_the_kind_every_wire_answers_and_how_long_to_wait():
    class SiatDown(Exception):
        failure_kind = FailureKind.UNAVAILABLE

    heard: list[ExecutionFailed] = []
    siat = UseFramework("failures-classified", log_after_execution=False)
    siat.on_failure(heard.append)

    @siat.feature(CommandIssueInvoice)
    class IssueInvoice(Feature):
        def execute(self, dto: CommandIssueInvoice) -> None:
            raise SiatDown("down")

    with pytest.raises(SiatDown):
        siat(CommandIssueInvoice())

    [failure] = heard
    assert (failure.kind, failure.retry_after) == (FailureKind.UNAVAILABLE.value, 1.0)


def test_a_failure_an_error_handler_answered_never_goes_out():
    heard: list[ExecutionFailed] = []
    billing = _billing("failures-answered")
    billing.on_failure(heard.append)
    billing.add_global_error_handler(lambda error: "answered")

    assert billing(CommandIssueInvoice()) == "answered"
    assert heard == []


def test_a_handler_that_only_logs_and_raises_lets_it_go_out():
    heard: list[ExecutionFailed] = []
    billing = _billing("failures-relogged")
    billing.on_failure(heard.append)

    def log_and_raise(error: Exception) -> Any:
        raise error

    billing.add_global_error_handler(log_and_raise)
    with pytest.raises(SiatUnavailable):
        billing(CommandIssueInvoice())

    assert len(heard) == 1


def test_a_failure_caught_inside_never_escaped():
    heard: list[ExecutionFailed] = []
    sales = UseFramework("failures-caught", log_after_execution=False)
    sales.on_failure(heard.append)

    @sales.feature(CommandIssueInvoice)
    class IssueInvoice(Feature):
        def execute(self, dto: CommandIssueInvoice) -> None:
            raise SiatUnavailable("down")

    @sales.app_service(CommandConfirmSale)
    class ConfirmSale(ApplicationService):
        def execute(self, dto: CommandConfirmSale) -> str:
            try:
                self.feature_bus.execute(CommandIssueInvoice())
            except SiatUnavailable:
                return "invoiced later"
            return "invoiced"

    assert sales(CommandConfirmSale()) == "invoiced later"
    assert heard == []


def test_a_nested_failure_goes_out_once_naming_where_it_happened_and_where_it_left():
    heard: list[ExecutionFailed] = []
    billing = _billing("failures-inner")
    sales = UseFramework("failures-outer", log_after_execution=False)
    sales.add_dependency("billing", billing)
    failures.subscribe(heard.append)
    billing.on_failure(heard.append)  # the same listener twice: heard once

    @sales.app_service(CommandConfirmSale)
    class ConfirmSale(ApplicationService):
        def execute(self, dto: CommandConfirmSale) -> None:
            self.billing(CommandIssueInvoice())  # type: ignore[attr-defined]

    with pytest.raises(SiatUnavailable):
        sales(CommandConfirmSale())

    [failure] = heard
    assert (failure.bus, failure.escaped_from, failure.use_case) == (
        "failures-inner",
        "failures-outer",
        "failures-inner.CommandIssueInvoice",
    )


def test_a_bus_hears_only_its_own_and_the_process_hears_all():
    of_billing: list[ExecutionFailed] = []
    of_process: list[ExecutionFailed] = []
    billing = _billing("failures-own")
    other = _billing("failures-other")
    billing.on_failure(of_billing.append)
    failures.subscribe(of_process.append)

    for bus in (billing, other):
        with pytest.raises(SiatUnavailable):
            bus(CommandIssueInvoice())

    assert [one.bus for one in of_billing] == ["failures-own"]
    assert [one.bus for one in of_process] == ["failures-own", "failures-other"]


def test_a_listener_that_raises_never_replaces_the_failure():
    heard: list[ExecutionFailed] = []
    billing = _billing("failures-broken-listener")

    @billing.on_failure
    def broken(failure: ExecutionFailed) -> None:
        raise RuntimeError("the pager is down")

    billing.on_failure(heard.append)

    with pytest.raises(SiatUnavailable):
        billing(CommandIssueInvoice())
    assert len(heard) == 1


def test_failures_published_to_another_bus_are_heard_as_any_event():
    incidents: list[ExecutionFailed] = []
    ops = UseFramework("failures-ops", log_after_execution=False)

    @ops.feature(ExecutionFailed)
    class OpenIncident(Feature):
        def execute(self, dto: ExecutionFailed) -> None:
            incidents.append(dto)

    billing = _billing("failures-published")
    billing.publish_failures(to=Publisher(SyncQueue(Subscriber(ops))))

    with pytest.raises(SiatUnavailable):
        billing(CommandIssueInvoice())

    [incident] = incidents
    assert incident.error_type == "SiatUnavailable"
    assert incident.name == "sincpro.execution.v1.failed"


def test_nobody_listening_costs_nothing_and_marks_nothing():
    billing = _billing("failures-unheard")

    with pytest.raises(SiatUnavailable) as raised:
        billing(CommandIssueInvoice())

    assert not hasattr(raised.value, "failure_id")


# --- completions --------------------------------------------------------------------------------


class ResponseReserveStock(DataTransferObject):
    reservation_id: str


class CommandReserveStock(DataTransferObject):
    order_id: str = "O-1"


def _warehouse(name: str) -> UseFramework:
    warehouse = UseFramework(name, log_after_execution=False)

    @warehouse.feature(CommandReserveStock)
    class ReserveStock(Feature):
        def execute(self, dto: CommandReserveStock) -> ResponseReserveStock:
            return ResponseReserveStock(reservation_id=f"R-{dto.order_id}")

    @warehouse.app_service(CommandConfirmSale)
    class ConfirmSale(ApplicationService):
        def execute(self, dto: CommandConfirmSale) -> str:
            self.feature_bus.execute(CommandReserveStock(order_id="O-2"))
            return "confirmed"

    return warehouse


def test_a_use_case_that_answered_goes_out_with_its_dto_and_response():
    heard: list[ExecutionCompleted] = []
    executions: list[Any] = []
    warehouse = _warehouse("completions-answered")
    warehouse.on_completion(heard.append)
    warehouse.on_completion(lambda _: executions.append(current_execution()))

    with warehouse.context({"tenant_id": "acme", "correlation_id": "flow-1"}):
        warehouse(CommandReserveStock(order_id="O-7"))

    [completed] = heard
    assert (completed.use_case, completed.bus, completed.level) == (
        "completions-answered.CommandReserveStock",
        "completions-answered",
        "feature",
    )
    assert completed.dto == {"order_id": "O-7"}
    assert completed.response == {"reservation_id": "R-O-7"}
    assert completed.response_type == "ResponseReserveStock"
    assert completed.execution_id == completed.causation_id == executions[0].execution_id
    assert completed.correlation_id == "flow-1"
    assert completed.context["tenant_id"] == "acme"
    assert completed.name == "sincpro.execution.v1.completed"


def test_every_use_case_an_application_service_runs_completes_on_its_own():
    heard: list[ExecutionCompleted] = []
    warehouse = _warehouse("completions-nested")
    warehouse.on_completion(heard.append)

    assert warehouse(CommandConfirmSale()) == "confirmed"

    assert [(one.use_case, one.level, one.response) for one in heard] == [
        ("completions-nested.CommandReserveStock", "feature", {"reservation_id": "R-O-2"}),
        ("completions-nested.CommandConfirmSale", "application_service", "confirmed"),
    ]
    feature, service = heard
    assert feature.correlation_id == service.correlation_id


def test_an_answer_an_error_handler_gave_is_not_a_completion():
    heard: list[ExecutionCompleted] = []
    billing = _billing("completions-handled")
    billing.on_completion(heard.append)
    billing.add_global_error_handler(lambda error: "answered")

    assert billing(CommandIssueInvoice()) == "answered"
    assert heard == []


def test_a_failure_is_no_completion():
    heard: list[ExecutionCompleted] = []
    billing = _billing("completions-failed")
    billing.on_completion(heard.append)

    with pytest.raises(SiatUnavailable):
        billing(CommandIssueInvoice())
    assert heard == []


def test_the_process_hears_every_bus_and_a_broken_listener_changes_nothing():
    of_process: list[ExecutionCompleted] = []
    warehouse = _warehouse("completions-process")

    @completions.subscribe
    def broken(completed: ExecutionCompleted) -> None:
        raise RuntimeError("the audit is down")

    completions.subscribe(of_process.append)

    assert warehouse(CommandReserveStock(order_id="O-9")) == ResponseReserveStock(
        reservation_id="R-O-9"
    )
    assert [one.bus for one in of_process] == ["completions-process"]


def test_completions_handed_to_a_bus_never_announce_their_own_hearing():
    trail: list[str] = []
    audit = UseFramework("completions-audit", log_after_execution=False)

    @audit.feature(ExecutionCompleted)
    class KeepTrail(Feature):
        def execute(self, dto: ExecutionCompleted) -> None:
            trail.append(dto.use_case)

    warehouse = _warehouse("completions-published")
    completions.subscribe(to_publisher(Publisher(SyncQueue(Subscriber(audit)))))

    warehouse(CommandReserveStock())

    assert trail == ["completions-published.CommandReserveStock"]


def test_nobody_listening_builds_nothing():
    warehouse = _warehouse("completions-unheard")
    assert completions.heard("completions-unheard") is False
    assert warehouse(CommandReserveStock(order_id="O-3")) == ResponseReserveStock(
        reservation_id="R-O-3"
    )
