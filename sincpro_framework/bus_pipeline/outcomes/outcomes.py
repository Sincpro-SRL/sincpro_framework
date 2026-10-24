"""`Outcomes`: what one bus announces when a use case ends, and who hears it."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sincpro_framework.bus_pipeline.listeners import Listeners
from sincpro_framework.bus_pipeline.outcomes.listeners import completions, failures
from sincpro_framework.sincpro_logger import logger

if TYPE_CHECKING:
    from sincpro_framework.context.domain.execution import Execution
    from sincpro_framework.context.domain.node import ContextNode

FAILURE_ID = "failure_id"
"""What an escaped exception carries: the id of the `ExecutionFailed` that went out with it."""

FAILED_AT = "__sincpro_failed_at__"
"""Where a bus leaves, on an exception, the use case it first failed in."""


@dataclass(frozen=True)
class FailedAt:
    """Where an exception first failed — the use case that raised it, and who hears of it."""

    execution: "Execution | None"
    dto: Any
    bus: str
    level: str
    node: "ContextNode"
    """Where it ran: what the flow carried there is what the failure says, not what the caller
    that receives it carries."""
    listeners: Listeners
    """The failures of the bus it failed in: a failure is told to them and to the process's."""


class Outcomes:
    """The outcomes of one bus: who hears them, and announcing one."""

    def __init__(self, bus: str) -> None:
        """Nobody listening yet, but the process's listeners of completions and of failures:
        they hear every bus."""
        self.bus = bus
        """The bounded context whose use cases it announces."""
        self.completed = Listeners(process=completions)
        self.failed = Listeners(process=failures)

    def failed_in(
        self, error: BaseException, dto: Any, level: str, node: "ContextNode"
    ) -> None:
        """The use case `error` was raised in — the innermost one, left on the exception for
        whoever announces it when it escapes.

            failed_in(ValueError("x"), CommandPay(...), "feature", node)
            ->  None; the error carries FailedAt(bus="billing", level="feature", ...)
            failed_in(<same error, again in its application service>)  ->  None, kept as was

        A node with no execution, or an exception that takes no attribute, is left untouched.
        """
        if node.execution is None or hasattr(error, FAILED_AT):
            return
        try:
            setattr(
                error,
                FAILED_AT,
                FailedAt(node.execution, dto, self.bus, level, node, self.failed),
            )
        except (AttributeError, TypeError):
            pass

    def answered(self, dto: Any, response: Any, level: str, node: "ContextNode") -> None:
        """Announce that a use case answered `response` for `dto` — to whoever listens.

            answered(CommandPay(amount=10), ResponsePay(id="p1"), "feature", node)
            ->  None; listeners heard ExecutionCompleted(response={"id": "p1"},
                response_type="ResponsePay")

        1. Nobody hears: nothing is built.
        2. The event of the execution, with what it was handed and what it gave — never one for
           an execution event: hearing one is not announced.
        Final: each listener, in turn, never raising — announcing never replaces the response.
        """
        if not self.completed.heard():
            return
        from sincpro_framework.bus_pipeline.events import ExecutionEvent, as_json_values
        from sincpro_framework.bus_pipeline.outcomes.events import ExecutionCompleted

        if isinstance(dto, ExecutionEvent):
            return
        try:
            self.completed.announce(
                ExecutionCompleted.of_execution(
                    dto,
                    self.bus,
                    level,
                    node.execution,
                    response=as_json_values(response),
                    response_type=type(response).__name__ if response is not None else "",
                )
            )
        except (
            Exception
        ):  # noqa: BLE001 - announcing a completion never replaces the response
            logger.exception(
                f"the completion of {type(dto).__name__} on '{self.bus}' could not be announced"
            )

    def escaped(self, error: BaseException, dto: Any, fault: bool | None = None) -> None:
        """The exception that left the whole call, told where the caller receives it.

            escaped(<ValueError failed in billing.CommandPay>, CommandCheckout(...))
            ->  None; billing's listeners heard ExecutionFailed(use_case="billing.CommandPay",
                error_type="ValueError", kind="internal", ...) and the error carries its id

        1. Where it first failed — the use case, its bus, its execution — else the call itself.
        2. Nobody hears the bus it failed in, nor the process: nothing is built.
        3. Its kind and the wait before a retry, as every wire answers it; whether it is a fault,
           as the bus judged it (`fault`) — the shared rule when it did not say.
        Final: each listener of that bus and of the process, in turn, never raising; the
        exception carries the event's id."""
        origin: FailedAt | None = getattr(error, FAILED_AT, None)
        listeners = origin.listeners if origin is not None else self.failed
        if not listeners.heard():
            return
        from sincpro_framework import exceptions
        from sincpro_framework.bus_pipeline.events import ExecutionEvent
        from sincpro_framework.bus_pipeline.outcomes.events import ExecutionFailed
        from sincpro_framework.common.failures import (
            is_fault,
            refined_failure_kind,
            retry_after,
        )

        failed_dto = origin.dto if origin is not None else dto
        if isinstance(failed_dto, ExecutionEvent) or isinstance(dto, ExecutionEvent):
            return
        kind, wait = exceptions.FailureKind.INTERNAL, None
        if isinstance(error, Exception):
            kind = refined_failure_kind(error)
            wait = retry_after(error, kind)
        try:
            event = ExecutionFailed.of_execution(
                failed_dto,
                origin.bus if origin is not None else self.bus,
                origin.level if origin is not None else "",
                origin.execution if origin is not None else None,
                origin.node if origin is not None else None,
                escaped_from=self.bus,
                error_type=type(error).__name__,
                error=str(error),
                kind=kind.value,
                fault=is_fault(error) if fault is None else fault,
                retry_after=wait.total_seconds() if wait is not None else None,
            )
            try:
                setattr(error, FAILURE_ID, event.id)
            except (AttributeError, TypeError):
                pass
            listeners.announce(event)
        except Exception:  # noqa: BLE001 - announcing a failure never replaces it
            logger.exception(
                f"the failure of {type(failed_dto).__name__} could not be announced"
            )
