"""`EventSourcedMixin`: an entity whose state is its events — no row of its own; rebuilt by
applying them in order, and written by appending new ones.

    @dataclass
    class Account(EventSourcedMixin, Entity):
        event_base = BankingDomainEvent          the table its events are kept in
        balance: int = 0

        def apply(self, event: DomainEvent) -> None:
            match event:
                case Deposited(amount=amount): self.balance += amount
                case Withdrawn(amount=amount): self.balance -= amount

        def withdraw(self, amount: int) -> None:
            self.happened(Withdrawn(amount=amount))     recorded, applied, numbered

    account = repository.get(Account, account_id)      its events, in order, applied
    account.withdraw(30)
    repository.save(account)                           the new events appended

**`version` counts its events.** Each new event carries `entity_version` — the entity's version
after it — and the store keeps one event per entity and version. Two writers who rebuilt the same
version both try to append the same next one, and the second is a `StaleAggregate`: the same
protection a row's `version` gives an entity that has one.

A class used this way gives every field a default: it is built empty and its events fill it.
"""

import dataclasses
from typing import TYPE_CHECKING, Any, ClassVar, Self

if TYPE_CHECKING:
    from sincpro_framework.ddd.events.domain_event import DomainEvent


class EventSourcedMixin:
    event_base: ClassVar[type]
    """The bounded context's base event class — the table its events are kept in."""

    version: int
    id: str

    def apply(self, event: "DomainEvent") -> None:
        """How one event changes the state. Every event the entity records goes through here,
        and so does every event it is rebuilt from."""
        raise NotImplementedError(
            f"{type(self).__name__} is event sourced: say how each of its events changes it, "
            "in apply(event)"
        )

    def happened(self, event: "DomainEvent") -> "DomainEvent":
        """Records the event as the next version of this entity and applies it."""
        numbered = dataclasses.replace(event, entity_version=self.version + 1)
        stamped = self.record(numbered)  # type: ignore[attr-defined]
        self.apply(stamped)
        self.version += 1
        return stamped

    @classmethod
    def rebuilt(cls, identity: Any, events: "list[DomainEvent]") -> Self | None:
        """The entity its events add up to, in order; `None` when it has none."""
        if not events:
            return None
        entity = cls(id=identity)  # type: ignore[call-arg]
        for one in sorted(events, key=lambda event: event.entity_version or 0):
            entity.apply(one)
            entity.version = one.entity_version or entity.version + 1
        return entity

    def unsaved_events(self) -> "list[DomainEvent]":
        """The events recorded since it was rebuilt — what a save appends."""
        return list(self.recorded_events())  # type: ignore[attr-defined]
