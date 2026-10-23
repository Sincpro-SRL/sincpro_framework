"""`Incomes`: what one bus announces when a use case begins, and who hears it."""

from typing import TYPE_CHECKING, Any

from sincpro_framework.bus_pipeline.incomes.listeners import starts
from sincpro_framework.bus_pipeline.listeners import Listeners
from sincpro_framework.sincpro_logger import logger

if TYPE_CHECKING:
    from sincpro_framework.context.domain.node import ContextNode


class Incomes:
    """The starts of one bus: who hears them, and announcing one."""

    def __init__(self, bus: str) -> None:
        self.bus = bus
        """The bounded context whose use cases it announces."""
        self.started = Listeners(process=starts)

    def began(self, dto: Any, level: str, node: "ContextNode") -> None:
        """1. Nobody hears: nothing is built.
        2. The event of the execution, which was handed `dto` — never one for an execution event:
           hearing one is not announced, or a listener that hands them to a bus would hear its own
           hearing without end.
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
