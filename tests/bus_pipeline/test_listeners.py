"""Who hears an execution belongs to the bus: a generation of it hears what it does, one added later
is heard, and the name every announcement carries is the one the bus was given."""

import subprocess
import sys
from typing import Any

import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.bus_pipeline.incomes.listeners import starts
from sincpro_framework.bus_pipeline.outcomes.listeners import completions, failures
from sincpro_framework.context import Level, use_context


class CommandReserveStock(DataTransferObject):
    order_id: str = "O-1"


@pytest.fixture(autouse=True)
def nobody_listens_after():
    yield
    starts.clear()
    completions.clear()
    failures.clear()


def _warehouse(name: str, labels: list[str] | None = None) -> UseFramework:
    warehouse = UseFramework(name, log_after_execution=False)

    @warehouse.feature(CommandReserveStock)
    class ReserveStock(Feature):
        def execute(self, dto: CommandReserveStock) -> None:
            if labels is not None:
                bus = use_context(Level.BUS)
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
