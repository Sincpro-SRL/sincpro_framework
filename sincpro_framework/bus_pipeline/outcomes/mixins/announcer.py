"""`OutcomeAnnouncerMixin`: what a bus mixes in to announce how each execution ended — every use
case that answered, and the failure that escaped the call.

Context: with nobody listening, nothing is built and the events are never loaded. The listeners
are asked first; what builds an event is imported only for someone who hears it.
"""

from logging import Logger
from typing import TYPE_CHECKING

from sincpro_framework.bus_pipeline.outcomes.listeners import completions, failures
from sincpro_framework.context.domain.node import ContextNode
from sincpro_framework.observability.failure import failure_of

if TYPE_CHECKING:
    from sincpro_framework.observability import Observability

FAILED_EXECUTION = "__sincpro_failed_execution__"
"""Where a bus leaves, on an exception, the execution it failed in."""


class OutcomeAnnouncerMixin:
    """What a bus adds to announce its outcomes — never raising into the execution."""

    logger: Logger
    observability: "Observability"

    def _failed_in(self, error: BaseException, node: ContextNode) -> None:
        """The execution `error` was raised in — the innermost one, left for whoever announces
        it."""
        if node.execution is not None and not hasattr(error, FAILED_EXECUTION):
            try:
                setattr(error, FAILED_EXECUTION, node.execution)
            except (AttributeError, TypeError):
                pass

    def _completed(
        self, dto: object, response: object, level: str, node: ContextNode
    ) -> None:
        """The use case answered: to whoever listens to this bus."""
        bus = self.observability.bus_name
        if not completions.heard(bus):
            return
        from sincpro_framework.bus_pipeline.outcomes.announcing import announce_completion

        try:
            announce_completion(dto, response, bus, level, node.execution)
        except (
            Exception
        ):  # noqa: BLE001 - announcing a completion never replaces the response
            self.logger.exception("the completion could not be announced")

    def _failure_escaped(self, error: BaseException, dto: object) -> None:
        """The failure that left the call: to whoever listens to the bus it happened in — the
        one observability recorded it in, else this one."""
        escaped_from = self.observability.bus_name
        origin = failure_of(error)
        bus = getattr(origin, "bus", "") or escaped_from
        if not failures.heard(bus):
            return
        from sincpro_framework.bus_pipeline.outcomes.announcing import announce_failure

        try:
            announce_failure(error, dto, bus, escaped_from, origin)
        except Exception:  # noqa: BLE001 - announcing a failure never replaces it
            self.logger.exception("the failure could not be announced")
