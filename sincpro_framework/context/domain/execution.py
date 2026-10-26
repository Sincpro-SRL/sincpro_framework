"""Execution identity — every execution knows who it is, what caused it and which flow it belongs
to (PRD_21).

    Execution(execution_id, causation_id, correlation_id, use_case, bus, level) · started_at
    Chain.after(cause, correlation, own)      the one rule an event and an execution share

**An execution and an event are two shapes of one chain.** Both carry `causation_id` — the id of
what directly caused them — and `correlation_id` — the flow — and both get them by `Chain.after`,
the rule `DomainEvent.caused_by` always had. An execution's own id is `execution_id`, an event's is
`id`: one id space, so an event's `causation_id` names the execution that produced it, and an
execution's names the event that started it.

The vocabulary only; which execution is in play is `sincpro_framework.context.services.executions`.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from sincpro_framework.common.ids import moment_of
from sincpro_framework.context.domain.keys import ContextKey

if TYPE_CHECKING:
    from sincpro_framework.context.domain.level import ContextLevel

CHAIN_KEYS = (ContextKey.EXECUTION_ID, ContextKey.CAUSATION_ID, ContextKey.CORRELATION_ID)
"""The context keys of an execution's identity — the same names a `DomainEvent` carries."""


@dataclass(frozen=True)
class Chain:
    """Where something caused sits: what directly caused it and the flow it belongs to."""

    causation_id: str | None
    correlation_id: str

    @classmethod
    def after(
        cls, cause_id: str | None, cause_correlation: str | None, own_id: str
    ) -> "Chain":
        """The chain of something caused by the cause named.

        Chain.after("E1", "C", "own")    ->  Chain("E1", "C")
        Chain.after("E1", None, "own")   ->  Chain("E1", "E1")     a cause nobody correlated
                                                                  heads its chain
        Chain.after(None, "C", "own")    ->  Chain(None, "C")      a request that named its flow
        Chain.after(None, None, "own")   ->  Chain(None, "own")    the head of a new chain
        """
        return cls(cause_id, cause_correlation or cause_id or own_id)


@dataclass(frozen=True)
class Execution:
    """One execution of a use case. What travels of it is its chain; the rest is local."""

    execution_id: str
    causation_id: str | None
    correlation_id: str
    use_case: str
    bus: str
    level: "ContextLevel"
    """`ContextLevel.APPLICATION` or `ContextLevel.FEATURE`."""

    @property
    def started_at(self) -> datetime | None:
        """Read off its UUID v7, never stored twice; `None` for an id minted in another
        format."""
        return moment_of(self.execution_id)

    def chain(self) -> dict[str, str]:
        """Its identity, as the context keys every signal of it carries."""
        keys: dict[str, str] = {
            ContextKey.EXECUTION_ID: self.execution_id,
            ContextKey.CORRELATION_ID: self.correlation_id,
        }
        if self.causation_id:
            keys[ContextKey.CAUSATION_ID] = self.causation_id
        return keys

    def handed_on(self) -> dict[str, str]:
        """What the next execution, or an event this one produces, is caused by."""
        return {
            ContextKey.CAUSATION_ID: self.execution_id,
            ContextKey.CORRELATION_ID: self.correlation_id,
        }
