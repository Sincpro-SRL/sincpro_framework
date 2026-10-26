"""`EventSourcedMixin`: an entity whose state is its events — no row of its own; rebuilt by
applying them in order, and written by appending new ones.

    @dataclass
    class Account(EventSourcedMixin, Entity):
        event_base = BankingDomainEvent          the table its events are stored in
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
after it — and the store holds one event per entity and version. Two writers who rebuilt the
same version both try to append the same next one, and the second is a `StaleAggregate`: the
same protection a row's `version` gives an entity that has one.

A class used this way gives every field a default: it is built empty and its events fill it.
"""

import dataclasses
from typing import TYPE_CHECKING, Any, ClassVar, Self

if TYPE_CHECKING:
    from sincpro_framework.ddd.events.domain_event import DomainEvent


class EventSourcedMixin:
    """Makes an entity's state the sum of its events: `happened` records and applies one,
    `rebuilt` replays them, and a save appends the ones not stored yet.

    Mixed in before `Entity`; the entity says how each event changes it in `apply` and names
    where its events are stored in `event_base`. `version` is the number of events applied.
    """

    event_base: ClassVar[type]
    """The bounded context's base event class — the table its events are stored in."""

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
        """Records the event as the next version of this entity and applies it.

            account.version                                   ->  3
            account.happened(Deposited(account_id="A-1", amount=50))
                ->  Deposited(…, entity_version=4)    # recorded, applied
            account.version                                   ->  4

        Context: the event is numbered before `apply` runs, so `apply` sees the version it
        makes; the stamped copy `record` answers is the one applied and handed back.
        """
        numbered = dataclasses.replace(event, entity_version=self.version + 1)
        stamped = self.record(numbered)  # type: ignore[attr-defined]
        self.apply(stamped)
        self.version += 1
        return stamped

    @classmethod
    def rebuilt(cls, identity: Any, events: "list[DomainEvent]") -> Self | None:
        """The entity its events add up to, in order; `None` when it has none.

            Account.rebuilt("A-1", [Deposited(amount=50, entity_version=2),
                                    Opened(entity_version=1)])
                ->  Account(id="A-1", balance=50, version=2)    # Opened applied first
            Account.rebuilt("A-1", [])   ->  None

        Context: built with its identity alone, then each event applied in `entity_version`
        order; an event with no version counts as the next one. Nothing is recorded again.
        """
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
