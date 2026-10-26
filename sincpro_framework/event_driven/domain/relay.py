"""What the outbox's relay talks in: what it hands each event to (`Publishes`), and what became
of the events of one pass (`Became`, `RelayPass`).

    relay.run_once()      ->  RelayPass(read=3, delivered=2, retried=1, parked=0, …)
"""

from enum import StrEnum
from typing import Any, Protocol

from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.sincpro_abstractions import DataTransferObject


class Publishes(Protocol):
    """What the relay hands each event to — a `Publisher` over any queue is one."""

    def publish(self, event: DomainEvent) -> Any:
        """Send `event` on; raising is a failed delivery, for the policy to decide."""
        ...


class Became(StrEnum):
    """What became of one event in a pass — a field of `RelayPass` each."""

    DELIVERED = "delivered"
    RETRIED = "retried"
    HELD = "held"
    PARKED = "parked"
    SKIPPED = "skipped"


class RelayPass(DataTransferObject):
    """One pass: what was read, and what became of it."""

    read: int = 0
    delivered: int = 0
    retried: int = 0
    held: int = 0
    parked: int = 0
    skipped: int = 0
