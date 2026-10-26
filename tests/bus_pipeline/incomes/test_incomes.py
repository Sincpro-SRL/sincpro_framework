"""A use case that begins goes out as an event: its DTO as it was handed, once the context providers
have run and before the interceptors — the call as its caller made it. It says nothing of how the
use case will end, and it observes: it never changes what the call answers."""

from typing import Any

import pytest

from sincpro_framework import (
    ApplicationService,
    DataTransferObject,
    Feature,
    UseFramework,
)
from sincpro_framework.bus_pipeline.incomes.events import ExecutionStarted
from sincpro_framework.bus_pipeline.incomes.listeners import start_listeners
from sincpro_framework.bus_pipeline.outcomes.events import ExecutionCompleted, ExecutionFailed
from sincpro_framework.bus_pipeline.outcomes.listeners import (
    completion_listeners,
    failure_listeners,
)
from sincpro_framework.context import requires_context
from sincpro_framework.context.services.executions import current_execution
from sincpro_framework.event_driven import Publisher, Subscriber, SyncQueue


class CommandReserveStock(DataTransferObject):
    order_id: str = "O-1"


class ResponseReserveStock(DataTransferObject):
    reservation_id: str


class CommandConfirmSale(DataTransferObject):
    pass


class SiatUnavailable(Exception):
    pass


@pytest.fixture(autouse=True)
def nobody_listens_after():
    yield
    start_listeners.clear()
    completion_listeners.clear()
    failure_listeners.clear()


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


def _failing(name: str) -> UseFramework:
    shop = UseFramework(name, log_after_execution=False)

    @shop.feature(CommandReserveStock)
    class ReserveStock(Feature):
        def execute(self, dto: CommandReserveStock) -> None:
            raise SiatUnavailable("SIAT is not answering")

    return shop


def test_a_use_case_that_begins_goes_out_with_its_dto_before_it_runs():
    order: list[str] = []
    executions: list[Any] = []
    heard: list[ExecutionStarted] = []
    shop = UseFramework("starts-began", log_after_execution=False)

    @shop.feature(CommandReserveStock)
    class ReserveStock(Feature):
        def execute(self, dto: CommandReserveStock) -> ResponseReserveStock:
            order.append("ran")
            executions.append(current_execution())
            return ResponseReserveStock(reservation_id="R-1")

    shop.on_start(lambda started: order.append("started"))
    shop.on_start(heard.append)
    shop.on_completion(lambda completed: order.append("completed"))

    with shop.context({"tenant_id": "acme", "correlation_id": "flow-1"}):
        shop(CommandReserveStock(order_id="O-7"))

    assert order == ["started", "ran", "completed"]
    [started] = heard
    assert (started.use_case, started.bus, started.level) == (
        "starts-began.CommandReserveStock",
        "starts-began",
        "feature",
    )
    assert started.dto == {"order_id": "O-7"}
    assert started.execution_id == started.causation_id == executions[0].execution_id
    assert started.correlation_id == "flow-1"
    assert started.context["tenant_id"] == "acme"
    assert started.name == "sincpro.execution.v1.started"


def test_a_start_and_its_end_name_the_same_execution():
    began: list[ExecutionStarted] = []
    ended: list[ExecutionCompleted | ExecutionFailed] = []
    warehouse = _warehouse("starts-paired")
    shop = _failing("starts-paired-failing")
    for bus in (warehouse, shop):
        bus.on_start(began.append)
        bus.on_completion(ended.append)
        bus.on_failure(ended.append)

    warehouse(CommandReserveStock())
    with pytest.raises(SiatUnavailable):
        shop(CommandReserveStock())

    assert [(one.use_case, one.execution_id) for one in began] == [
        (one.use_case, one.execution_id) for one in ended
    ]
    assert [type(one) for one in ended] == [ExecutionCompleted, ExecutionFailed]


def test_every_use_case_an_application_service_runs_begins_on_its_own():
    order: list[str] = []
    warehouse = _warehouse("starts-nested")
    warehouse.on_start(lambda started: order.append(f"start {started.level}"))
    warehouse.on_completion(lambda completed: order.append(f"end {completed.level}"))

    warehouse(CommandConfirmSale())

    assert order == [
        "start application_service",
        "start feature",
        "end feature",
        "end application_service",
    ]


def test_a_start_carries_what_the_context_providers_derived():
    heard: list[ExecutionStarted] = []
    shop = UseFramework("starts-providers", log_after_execution=False)

    @shop.context_provider(needs=["nit_id"], gives=["region"])
    def region_of(context: Any) -> dict[str, str]:
        return {"region": "bo"}

    @shop.feature(CommandReserveStock)
    class ReserveStock(Feature):
        def execute(self, dto: CommandReserveStock) -> None:
            return None

    shop.on_start(heard.append)

    with shop.context({"nit_id": "123"}):
        shop(CommandReserveStock())

    assert heard[0].context["region"] == "bo"


def test_a_start_is_followed_by_the_failure_that_escapes():
    order: list[str] = []
    shop = _failing("starts-failing")
    shop.on_start(lambda started: order.append("started"))
    shop.on_failure(lambda failure: order.append("failed"))

    with pytest.raises(SiatUnavailable):
        shop(CommandReserveStock())

    assert order == ["started", "failed"]


def test_a_use_case_a_missing_requirement_stops_announces_no_start():
    heard: list[ExecutionStarted] = []
    shop = UseFramework("starts-required", log_after_execution=False)

    @shop.feature(CommandReserveStock)
    @requires_context("TOKEN")
    class ReserveStock(Feature):
        def execute(self, dto: CommandReserveStock) -> None:
            return None

    shop.on_start(heard.append)

    with pytest.raises(Exception):
        shop(CommandReserveStock())
    assert heard == []


# --- where the income stands: before the interceptors, the call as its caller made it -----------


def test_the_income_is_the_dto_as_it_was_handed_not_as_an_interceptor_adjusted_it():
    heard: list[ExecutionStarted] = []
    handled: list[str] = []
    shop = UseFramework("starts-adjusted", log_after_execution=False)

    @shop.feature(CommandReserveStock)
    class ReserveStock(Feature):
        def execute(self, dto: CommandReserveStock) -> None:
            handled.append(dto.order_id)

    @shop.interceptor(CommandReserveStock)
    def adjust(dto: CommandReserveStock, call_next: Any) -> Any:
        return call_next(dto.model_copy(update={"order_id": "ADJUSTED"}))

    shop.on_start(heard.append)

    shop(CommandReserveStock(order_id="O-7"))

    assert heard[0].dto == {"order_id": "O-7"}
    assert handled == ["ADJUSTED"]


def test_an_interceptor_that_answers_by_itself_still_starts_and_completes_the_call():
    began: list[ExecutionStarted] = []
    done: list[ExecutionCompleted] = []
    ran: list[str] = []
    shop = UseFramework("starts-cached", log_after_execution=False)

    @shop.feature(CommandReserveStock)
    class ReserveStock(Feature):
        def execute(self, dto: CommandReserveStock) -> ResponseReserveStock:
            ran.append("handler")
            return ResponseReserveStock(reservation_id="from the handler")

    @shop.interceptor(CommandReserveStock)
    def cached(dto: CommandReserveStock, call_next: Any) -> Any:
        return ResponseReserveStock(reservation_id="from the cache")

    shop.on_start(began.append)
    shop.on_completion(done.append)

    shop(CommandReserveStock())

    assert ran == []
    assert len(began) == 1
    assert done[0].response == {"reservation_id": "from the cache"}


def test_an_interceptor_that_vetoes_leaves_a_start_and_then_a_failure():
    order: list[str] = []
    warehouse = _warehouse("starts-vetoed")

    @warehouse.interceptor(CommandReserveStock)
    def veto(dto: CommandReserveStock, call_next: Any) -> Any:
        raise SiatUnavailable("not today")

    warehouse.on_start(lambda started: order.append("started"))
    warehouse.on_failure(lambda failure: order.append("failed"))

    with pytest.raises(SiatUnavailable):
        warehouse(CommandReserveStock())

    assert order == ["started", "failed"]


# --- who hears it --------------------------------------------------------------------------------


def test_a_bus_hears_only_its_own_starts_and_a_broken_listener_changes_nothing():
    of_bus: list[ExecutionStarted] = []
    of_process: list[ExecutionStarted] = []
    warehouse = _warehouse("starts-own")
    other = _warehouse("starts-other")
    warehouse.on_start(of_bus.append)

    @warehouse.on_start
    def broken(started: ExecutionStarted) -> None:
        raise RuntimeError("the audit is down")

    start_listeners.subscribe(of_process.append)

    for bus in (warehouse, other):
        assert bus(CommandReserveStock(order_id="O-9")) == ResponseReserveStock(
            reservation_id="R-O-9"
        )

    assert [one.bus for one in of_bus] == ["starts-own"]
    assert [one.bus for one in of_process] == ["starts-own", "starts-other"]


def test_starts_handed_to_a_bus_never_announce_their_own_hearing():
    trail: list[str] = []
    audit = UseFramework("starts-audit", log_after_execution=False)

    @audit.feature(ExecutionStarted)
    class KeepTrail(Feature):
        def execute(self, dto: ExecutionStarted) -> None:
            trail.append(dto.use_case)

    warehouse = _warehouse("starts-published")
    warehouse.publish_starts(to=Publisher(SyncQueue(Subscriber(audit))))

    warehouse(CommandReserveStock())

    assert trail == ["starts-published.CommandReserveStock"]


def test_a_bus_nobody_listens_to_answers_as_always():
    warehouse = _warehouse("starts-unheard")

    assert warehouse(CommandReserveStock(order_id="O-3")) == ResponseReserveStock(
        reservation_id="R-O-3"
    )
