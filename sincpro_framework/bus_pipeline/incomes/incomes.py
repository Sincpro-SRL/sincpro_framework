"""`Incomes`: what one bus announces when a use case begins, and who hears it."""

from typing import TYPE_CHECKING, Any

from sincpro_framework.bus_pipeline.incomes.listeners import start_listeners
from sincpro_framework.bus_pipeline.listeners import Listeners
from sincpro_framework.sincpro_logger import logger

if TYPE_CHECKING:
    from sincpro_framework.context import ContextNode


class Incomes:
    """The starts of one bus: who hears them, and announcing one."""

    def __init__(self, bus: str) -> None:
        """Nobody listening yet but the process's listeners of starts, who hear every bus."""
        self.bus = bus
        """The bounded context whose use cases it announces."""
        self.started = Listeners(process=start_listeners)

    def began(self, dto: Any, level: str, node: "ContextNode") -> None:
        """Announce that a use case began running `dto` at `level` — to whoever listens.

            began(CommandReserve(sku="A1"), "feature", node)
            ->  None; each listener heard ExecutionStarted(use_case="stock.CommandReserve")
            began(ExecutionStarted(...), "feature", node)    ->  None, nobody told

        1. Nobody hears: nothing is built.
        2. The event of the execution, which was handed `dto` — never one for an execution
           event: hearing one is not announced, or a listener that hands them to a bus would
           hear its own hearing without end.
        Final: each listener, in turn, never raising — announcing never stops the use case."""
        if not self.started.heard():
            return
        from sincpro_framework.bus_pipeline.events import ExecutionEvent
        from sincpro_framework.bus_pipeline.incomes.events import ExecutionStarted

        if isinstance(dto, ExecutionEvent):
            return
        try:
            self.started.announce(
                ExecutionStarted.of_execution(dto, self.bus, level, node.execution)
            )
        except Exception:  # noqa: BLE001 - announcing a start never stops the use case
            logger.exception(
                f"the start of {type(dto).__name__} on '{self.bus}' could not be announced"
            )
