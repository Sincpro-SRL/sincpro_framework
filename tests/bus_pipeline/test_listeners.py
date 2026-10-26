"""Who hears an execution belongs to the bus: a generation of it hears what it does, one added later
is heard, and the name every announcement carries is the one the bus was given."""

import subprocess
import sys
from typing import Any

import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.bus_pipeline.incomes.listeners import start_listeners
from sincpro_framework.bus_pipeline.outcomes.listeners import (
    completion_listeners,
    failure_listeners,
)
from sincpro_framework.context import ContextLevel, use_context


class CommandReserveStock(DataTransferObject):
    order_id: str = "O-1"


@pytest.fixture(autouse=True)
def nobody_listens_after():
    yield
    start_listeners.clear()
    completion_listeners.clear()
    failure_listeners.clear()


def _warehouse(name: str, labels: list[str] | None = None) -> UseFramework:
    warehouse = UseFramework(name, log_after_execution=False)

    @warehouse.feature(CommandReserveStock)
    class ReserveStock(Feature):
        def execute(self, dto: CommandReserveStock) -> None:
            if labels is not None:
                bus = use_context(ContextLevel.BUS)
                labels.append(bus.label if bus is not None else "")

    return warehouse


def test_a_generation_of_the_bus_hears_what_this_one_does():
    heard: list[str] = []
    warehouse = _warehouse("listeners-generation")
    warehouse.on_start(lambda started: heard.append("start"))
    warehouse.on_completion(lambda completed: heard.append("completion"))

    warehouse.fresh()(CommandReserveStock())

    assert heard == ["start", "completion"]


def test_a_listener_added_after_the_bus_was_built_is_heard():
    heard: list[Any] = []
    warehouse = _warehouse("listeners-late")
    warehouse(CommandReserveStock())

    warehouse.on_start(heard.append)
    warehouse(CommandReserveStock())

    assert len(heard) == 1


def test_the_name_a_bus_was_given_is_the_one_everything_about_it_carries():
    heard: list[Any] = []
    labels: list[str] = []
    warehouse = _warehouse("listeners-named", labels)
    warehouse.on_start(heard.append)

    warehouse(CommandReserveStock())

    assert warehouse.name == "listeners-named"
    assert warehouse.observability.bus_name == warehouse.name
    assert heard[0].bus == warehouse.name
    assert heard[0].use_case == f"{warehouse.name}.CommandReserveStock"
    assert labels == [warehouse.name]
    assert warehouse.fresh().name == warehouse.name


def test_a_bus_nobody_listens_to_never_builds_an_event():
    """The events load the DDD layer: a process nobody listens in never loads them."""
    program = """
import sys
from sincpro_framework import DataTransferObject, Feature, UseFramework

class CommandReserveStock(DataTransferObject):
    pass

shop = UseFramework("listeners-unheard", log_after_execution=False)

@shop.feature(CommandReserveStock)
class ReserveStock(Feature):
    def execute(self, dto: CommandReserveStock) -> None:
        return None

shop(CommandReserveStock())
print("sincpro_framework.bus_pipeline.events" in sys.modules)
"""
    ran = subprocess.run(
        [sys.executable, "-I", "-c", program], capture_output=True, text=True, check=True
    )

    assert ran.stdout.strip().splitlines()[-1] == "False"


# --- hearing less -----------------------------------------------------------------------------


class CommandRelease(DataTransferObject):
    order_id: str = "O-1"
    amount: int = 0


class StockUnavailable(Exception):
    pass


def _failing_warehouse(name: str) -> UseFramework:
    warehouse = _warehouse(name)

    @warehouse.feature(CommandRelease)
    class Release(Feature):
        def execute(self, dto: CommandRelease) -> None:
            raise StockUnavailable(dto.order_id)

    return warehouse


def test_a_listener_hears_only_the_use_cases_it_names():
    heard: list[str] = []
    warehouse = _warehouse("listeners-use-cases")

    @warehouse.feature(CommandRelease)
    class Release(Feature):
        def execute(self, dto: CommandRelease) -> None:
            pass

    warehouse.on_completion(
        lambda done: heard.append(done.use_case), use_cases=[CommandRelease]
    )
    warehouse(CommandReserveStock())
    warehouse(CommandRelease())

    assert heard == ["listeners-use-cases.CommandRelease"]


def test_a_use_case_may_be_named_by_its_registered_name():
    heard: list[str] = []
    warehouse = _failing_warehouse("listeners-by-name")
    warehouse.on_failure(
        lambda failed: heard.append(failed.use_case),
        use_cases=["listeners-by-name.CommandRelease"],
    )

    with pytest.raises(StockUnavailable):
        warehouse(CommandRelease())

    assert heard == ["listeners-by-name.CommandRelease"]


def test_when_filters_by_any_field_of_the_event_by_its_path():
    from sincpro_framework.ddd.criteria import Condition, Operator

    heard: list[str] = []
    warehouse = _warehouse("listeners-when")
    warehouse.on_start(
        lambda started: heard.append(started.context["tenant_id"]),
        when=Condition(field="context.tenant_id", value="acme"),
    )
    warehouse.on_completion(
        lambda done: heard.append(f"big {done.dto['order_id']}"),
        when=[
            Condition(field="dto.order_id", operator=Operator.STARTS_WITH, value="O-"),
            Condition(field="context.tenant_id", operator=Operator.NE, value="globex"),
        ],
    )

    with warehouse.context({"tenant_id": "acme"}):
        warehouse(CommandReserveStock(order_id="O-7"))
    with warehouse.context({"tenant_id": "globex"}):
        warehouse(CommandReserveStock(order_id="O-8"))

    assert heard == ["acme", "big O-7"]


def test_when_may_be_the_json_a_configuration_holds():
    heard: list[Any] = []
    warehouse = _failing_warehouse("listeners-json")
    warehouse.on_failure(
        heard.append, when={"field": "dto.amount", "operator": ">", "value": 100}
    )

    for amount in (50, 500):
        with pytest.raises(StockUnavailable):
            warehouse(CommandRelease(amount=amount))

    assert [failed.dto["amount"] for failed in heard] == [500]


def test_a_path_the_event_does_not_hold_reads_none_and_hears_nothing():
    from sincpro_framework.ddd.criteria import Condition

    heard: list[Any] = []
    warehouse = _warehouse("listeners-missing")
    warehouse.on_start(heard.append, when=Condition(field="context.pos_id", value="P-1"))

    warehouse(CommandReserveStock())

    assert heard == []


def test_the_decorator_takes_the_same_filters():
    heard: list[str] = []
    warehouse = _failing_warehouse("listeners-decorator")

    @warehouse.on_failure(use_cases=[CommandRelease])
    def alert(failed: Any) -> None:
        heard.append(failed.error_type)

    with pytest.raises(StockUnavailable):
        warehouse(CommandRelease())
    warehouse(CommandReserveStock())

    assert heard == ["StockUnavailable"]


def test_a_publisher_is_handed_only_what_its_filter_lets_through():
    published: list[str] = []

    class Recording:
        def publish(self, event: Any) -> None:
            published.append(event.use_case)

    warehouse = _warehouse("listeners-publish")
    warehouse.publish_starts(to=Recording(), use_cases=[CommandReserveStock])
    warehouse.fresh()(CommandReserveStock())

    assert published == ["listeners-publish.CommandReserveStock"]


def test_the_process_listens_with_the_same_filters():
    from sincpro_framework.ddd.criteria import Condition

    heard: list[str] = []
    failure_listeners.subscribe(
        heard.append, when=Condition(field="error_type", value="StockUnavailable")
    )
    warehouse = _failing_warehouse("listeners-process")

    with pytest.raises(StockUnavailable):
        warehouse(CommandRelease())

    assert len(heard) == 1


def test_a_filter_that_cannot_be_read_is_logged_and_never_reaches_the_call():
    from sincpro_framework.ddd.criteria import Condition, Operator

    heard: list[Any] = []
    warehouse = _warehouse("listeners-broken-filter")
    warehouse.on_start(
        heard.append, when=Condition(field="dto.order_id", operator=Operator.GT, value=3)
    )

    warehouse(CommandReserveStock())

    assert heard == []
