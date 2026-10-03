"""Execution identity — every execution knows who it is, what caused it and which flow it belongs
to (PRD_21).

    Execution(execution_id, causation_id, correlation_id, use_case, bus, level) · started_at
    chained(cause, correlation, own)      the one rule an event and an execution share

**An execution and an event are two shapes of one chain.** Both carry `causation_id` — the id of
what directly caused them — and `correlation_id` — the flow — and both get them by `chained`, the
rule `DomainEvent.caused_by` always had. An execution's own id is `execution_id`, an event's is
`id`: one id space, so an event's `causation_id` names the execution that produced it, and an
execution's names the event that started it.

The vocabulary only; which execution is in play is `sincpro_framework.context.infrastructure`.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from sincpro_framework.ids import moment_of

if TYPE_CHECKING:
    from sincpro_framework.context.domain.level import Level

EXECUTION_ID = "execution_id"
CAUSATION_ID = "causation_id"
CORRELATION_ID = "correlation_id"
CHAIN_KEYS = (EXECUTION_ID, CAUSATION_ID, CORRELATION_ID)
"""The context keys of an execution's identity — the same names a `DomainEvent` carries."""

CONTEXT_NODE = "sincpro.context_node"
"""The node in play, handed on beside the identity when a store shares the context: a receiving bus
that set the same store reads the sender's chain from there."""
CONTEXT_NODE_HEADER = "x-context-node"
"""The header a message carries `CONTEXT_NODE` in — so a receiver needs no other header to read the
sender's chain."""

IDENTITY_HEADERS = {
    "x-correlation-id": CORRELATION_ID,
    "x-causation-id": CAUSATION_ID,
    "x-execution-id": EXECUTION_ID,
}
"""The headers an entrypoint reads the identity from — HTTP and gRPC alike, lowercase."""


@dataclass(frozen=True)
class Execution:
    """One execution of a use case. What travels of it is its chain; the rest is local."""

    execution_id: str
    causation_id: str | None
    correlation_id: str
    use_case: str
    bus: str
    level: "Level"
    """`Level.APPLICATION` or `Level.FEATURE`."""

    @property
    def started_at(self) -> datetime | None:
        """Read off its UUID v7, never kept twice; `None` for an id minted in another format."""
        return moment_of(self.execution_id)

    def chain(self) -> dict[str, str]:
        """Its identity, as the context keys every signal of it carries."""
        keys = {EXECUTION_ID: self.execution_id, CORRELATION_ID: self.correlation_id}
        if self.causation_id:
            keys[CAUSATION_ID] = self.causation_id
        return keys

    def handed_on(self) -> dict[str, str]:
        """What the next execution, or an event this one produces, is caused by."""
        return {CAUSATION_ID: self.execution_id, CORRELATION_ID: self.correlation_id}


def chained(
    cause_id: str | None, cause_correlation: str | None, own_id: str
) -> tuple[str | None, str]:
    """`(causation_id, correlation_id)` of something caused by the cause named.

    cause E1 with correlation C       →  (E1, C)
    cause E1 that carried none        →  (E1, E1)     a cause nobody correlated heads its chain
    no cause, a correlation given     →  (None, C)    a request that named its flow
    nothing                           →  (None, own)  the head of a new chain
    """
    return cause_id, cause_correlation or cause_id or own_id
