"""The verdict on one delivery — what the gateway tells the broker, decided from what happened and
never from FastStream's per-broker default (PRD_15 §1.3, §5.2).

| What happened | Verdict | Settled |
|---|---|---|
| the bus handled it | `ACK` | ack |
| a redelivery of a message the inbox saw completed | `REPLAY` | ack, the bus not run |
| an event nobody here handles, on a shared channel | `SKIP` | ack |
| conflict, in progress, exhausted, unavailable, internal, the time limit | `RETRY` | nack, or a copy with the attempt counted — then dead-letter at `max_attempts` |
| undecodable, invalid, unauthenticated, permission denied, a producer not allowed, not found, key reused, domain, expired | `DEAD_LETTER` | the dead-letter channel, or the broker's own |

Context: pure — no broker here. The classification is `common.failures`', shared by every
wire; this module is the queue column of its table.
"""

from enum import StrEnum

from pydantic import ConfigDict

from sincpro_framework.common.failures import PERMANENT
from sincpro_framework.exceptions import FailureKind
from sincpro_framework.sincpro_abstractions import DataTransferObject


class Settlement(StrEnum):
    ACK = "ack"
    REPLAY = "replay"
    SKIP = "skip"
    RETRY = "retry"
    DEAD_LETTER = "dead_letter"


SECURITY_KINDS = frozenset({FailureKind.UNAUTHENTICATED, FailureKind.PERMISSION_DENIED})
"""Kinds that are also a security event — logged as one."""


class Verdict(DataTransferObject):
    """How one delivery was settled, and why — what `QueueGateway.consume` answers."""

    model_config = ConfigDict(frozen=True)

    settlement: Settlement
    kind: FailureKind | None = None
    reason: str | None = None
    """UPPER_SNAKE, stable: the error's class when the producer may read it, the kind
    otherwise, or the gateway's own (`PRODUCER_NOT_ALLOWED`, `EXPIRED`, `UNDECODABLE`)."""
    attempts: int = 1
    command: str | None = None
    """The message's type — the Command or event it was consumed as, when known."""


def settlement_of(kind: FailureKind) -> Settlement:
    """The queue column of PRD_15 §1.3: dead-letter what fails the same way again, retry the
    rest."""
    return Settlement.DEAD_LETTER if kind in PERMANENT else Settlement.RETRY


def failed(
    kind: FailureKind, reason: str, attempts: int, max_attempts: int, command: str | None
) -> Verdict:
    """The verdict on a failure — a retry past `max_attempts` becomes a dead letter, its
    reason kept."""
    settlement = settlement_of(kind)
    if settlement == Settlement.RETRY and attempts >= max_attempts:
        settlement = Settlement.DEAD_LETTER
    return Verdict(
        settlement=settlement, kind=kind, reason=reason, attempts=attempts, command=command
    )


WORST_FIRST = (
    Settlement.RETRY,
    Settlement.DEAD_LETTER,
    Settlement.ACK,
    Settlement.REPLAY,
    Settlement.SKIP,
)
"""How the verdicts of several contexts on one delivery combine: a retry first — the inbox
keeps it from re-running the contexts that completed — then a dead letter, then a run."""


def combined(verdicts: list[Verdict]) -> Verdict:
    """The one verdict a delivery is settled by, when several contexts handled it."""
    return min(verdicts, key=lambda one: WORST_FIRST.index(one.settlement))
