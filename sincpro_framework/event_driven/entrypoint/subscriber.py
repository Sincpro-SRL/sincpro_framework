"""The buses that hear an event, handed in explicitly.

    subscriber = Subscriber(planning, assurance)
    subscriber.handle(event)                     →  what each bus that knows it answered
    await subscriber.get_async_subscriber().handle(event)

A bus declares nothing here: its registry already says which DTOs it answers, an event is a
DTO, and the subscriber executes the bus with it. `@bus.feature([SomeCommand, SomeEvent])` is
the whole subscription. The async form runs each bus through its own `get_async_bus()`, the
same way a caller that is `async def` runs a command.

**One bus failing never keeps the event from the others.** Every bus that answers the event is
run, and what failed comes out once all of them ran: the error itself when one bus failed, an
`ExceptionGroup` of them when several did. A delivery
that failed is delivered again — by the relay, by the broker — to every bus that answers it,
those that succeeded included: delivery is at least once, so a handler of an event is
idempotent. With `receipts` — a `KeyValueStore`, shared by the replicas — the ones that succeeded
are left out of the redelivery: only the bus that failed runs again.
"""

import json
from datetime import timedelta
from typing import Any

from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.store import KeyValueStore
from sincpro_framework.use_bus import UseFramework

RECEIPT = "subscriber"
"""What a receipt's key starts with: `subscriber:<bus>:<event id>`."""


class Subscriber:
    """Hands an event to every bus, of the ones given, that answers its wire name."""

    def __init__(
        self,
        *buses: UseFramework,
        receipts: KeyValueStore | None = None,
        keep_for: timedelta = timedelta(days=1),
    ) -> None:
        """`receipts` keeps which bus handled which event, for `keep_for` — longer than a
        redelivery can take — so a redelivery runs again only the buses that failed. Without
        one, every bus that answers the event runs again."""
        self.buses: list[UseFramework] = list(buses)
        self.receipts = receipts
        self.keep_for = keep_for

    def listeners(self, name: str) -> list[UseFramework]:
        """The buses that answer this event name, in the order they were given."""
        return [bus for bus in self.buses if name in bus.dto_registry]

    def event_type(self, name: str) -> type[DomainEvent] | None:
        """The class an event of that name rebuilds as, or `None` when no bus knows it.

            Subscriber(planning, assurance).event_type("billing.InvoicePaid")
                ->  planning's InvoicePaid    # the first bus that registered the name
            subscriber.event_type("billing.Unknown")         ->  None
            subscriber.event_type("billing.CommandPay")      ->  None    # not an event

        Context: a name a bus registered for a Command or a Query is no event, passed by.
        """
        for bus in self.buses:
            found = bus.dto_registry.get(name)
            if found is not None and issubclass(found, DomainEvent):
                return found
        return None

    def as_known_by(self, bus: UseFramework, event: DomainEvent) -> DomainEvent:
        """This event as *that* bus declared it — its own class for the same wire name.

        **An event has two identities and only one of them travels.** The wire name crosses a
        process and a bounded context; the Python class cannot, because a context that may not
        import the one that published it declares its own class under the same name. That is
        what `name` is for.

        The bus is chosen by name and then dispatches by class, so handing it the publisher's
        instance asks it for a class it never registered: the event is silently not delivered,
        and in a background worker the refusal is one log line in another process. The registry
        already answers this — it is keyed by name and holds the class this bus knows — so the
        event is rebuilt from it rather than passed through.

        Left alone when the bus declared the very class that was published, which is every
        event that never left its own context.
        """
        declared = bus.dto_registry.get(event.name)
        if declared is None or type(event) is declared:
            return event
        return declared.from_json(json.loads(event.as_json()))

    def heard_already(self, bus: UseFramework, event: DomainEvent) -> bool:
        """Whether `bus` already handled this event — a redelivery after another bus failed."""
        return (
            self.receipts is not None
            and self.receipts.get(f"{RECEIPT}:{bus.name}:{event.id}") is not None
        )

    def save_receipt(self, bus: UseFramework, event: DomainEvent) -> None:
        """Note that `bus` handled this event, for `keep_for` — nothing without `receipts`."""
        if self.receipts is not None:
            self.receipts.set(f"{RECEIPT}:{bus.name}:{event.id}", b"1", self.keep_for)

    def handle(self, event: DomainEvent) -> list[Any]:
        """The event to every bus that answers it, now — but the ones a receipt says already
        did; what each one returned, in order — or, once every bus ran, what failed: the error
        when one did, an `ExceptionGroup` when several did.

            Subscriber(planning, assurance).handle(InvoicePaid(…))
                ->  [<planning's answer>, <assurance's answer>]
            # assurance raised  ->  raises that error, after planning ran and kept its receipt
            # both raised  ->  raises ExceptionGroup("billing.InvoicePaid failed in 2 buses")
            # redelivered, planning's receipt kept  ->  only assurance runs again

        1. The buses that answer the event's name, in the order they were given.
        2. For each:
           2.1 one whose receipt says it handled this event already is passed by;
           2.2 it is handed the event as it declared it (`as_known_by`) — a failure is noted
               and the next bus still runs;
           2.3 a bus that answered gets its receipt, its answer kept.
        3. One failure: raised as it is.
        4. Several: raised together, as an `ExceptionGroup`.
        Final: every answer, in the order the buses ran.
        """
        answers: list[Any] = []
        failures: list[Exception] = []
        for bus in self.listeners(event.name):
            if self.heard_already(bus, event):
                continue
            try:
                answers.append(bus(self.as_known_by(bus, event)))
            except Exception as error:  # noqa: BLE001 - the other buses still hear it
                failures.append(error)
                continue
            self.save_receipt(bus, event)
        if len(failures) == 1:
            raise failures[0]
        if failures:
            raise ExceptionGroup(f"{event.name} failed in {len(failures)} buses", failures)
        return answers

    def get_async_subscriber(self) -> "AsyncSubscriber":
        """The same subscriber for an `async def` caller."""
        return AsyncSubscriber(self)


class AsyncSubscriber:
    """`Subscriber` for an `async def` caller — each bus through its `get_async_bus()`."""

    def __init__(self, subscriber: Subscriber) -> None:
        """The async face of `subscriber` — its buses, receipts and rebuilding are reused."""
        self._subscriber = subscriber

    async def handle(self, event: DomainEvent) -> list[Any]:
        """The event to every bus that answers it, each through its async facade — or, once
        every bus ran, what failed: the error when one did, an `ExceptionGroup` when several
        did.

            await subscriber.get_async_subscriber().handle(InvoicePaid(…))
                ->  [<planning's answer>, <assurance's answer>]
            # both raised  ->  raises ExceptionGroup("billing.InvoicePaid failed in 2 buses")

        1. The buses that answer the event's name, in the order they were given.
        2. For each:
           2.1 one whose receipt says it handled this event already is passed by;
           2.2 the event rebuilt as the bus declared it (`as_known_by`) — outside the guard,
               so a rebuild that fails stops the whole delivery at once;
           2.3 awaited on the bus's async facade — a failure is noted and the next bus runs;
           2.4 a bus that answered gets its receipt, its answer kept.
        3. One failure: raised as it is.
        4. Several: raised together, as an `ExceptionGroup`.
        Final: every answer, in the order the buses ran.
        """
        answers: list[Any] = []
        failures: list[Exception] = []
        for bus in self._subscriber.listeners(event.name):
            if self._subscriber.heard_already(bus, event):
                continue
            known = self._subscriber.as_known_by(bus, event)
            try:
                answers.append(await bus.get_async_bus()(known))
            except Exception as error:  # noqa: BLE001 - the other buses still hear it
                failures.append(error)
                continue
            self._subscriber.save_receipt(bus, event)
        if len(failures) == 1:
            raise failures[0]
        if failures:
            raise ExceptionGroup(f"{event.name} failed in {len(failures)} buses", failures)
        return answers
