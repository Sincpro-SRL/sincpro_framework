"""The buses that hear an event, handed in explicitly.

    subscriber = Subscriber(planning, assurance)
    subscriber.handle(event)                     →  what each bus that knows it answered
    await subscriber.get_async_subscriber().handle(event)

A bus declares nothing here: its registry already says which DTOs it answers, an event is a
DTO, and the subscriber executes the bus with it. `@bus.feature([SomeCommand, SomeEvent])` is
the whole subscription.

The work is `EventDispatch`'s: every bus that answers the event runs, whatever another raised,
and with `receipts` — a `KeyValueStore`, shared by the replicas — a redelivery runs only the bus
that failed.
"""

from datetime import timedelta
from typing import Any

from sincpro_framework.data_layer.store import KeyValueStore
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.event_driven.services.dispatch import AsyncEventDispatch, EventDispatch
from sincpro_framework.use_bus import UseFramework


class AsyncSubscriber:
    """`Subscriber` for an `async def` caller — each bus through its `get_async_bus()`."""

    def __init__(self, subscriber: "Subscriber") -> None:
        """The async face of `subscriber` — its buses, receipts and rebuilding are reused."""
        self._dispatch = AsyncEventDispatch(subscriber.dispatch)

    async def handle(self, event: DomainEvent) -> list[Any]:
        """The event to every bus that answers it, each through its async facade — or, once
        every bus ran, what failed: the error when one did, an `ExceptionGroup` when several
        did.

            await subscriber.get_async_subscriber().handle(InvoicePaid(…))
                ->  [<planning's answer>, <assurance's answer>]
        """
        return await self._dispatch.handle(event)


class Subscriber:
    """Hands an event to every bus, of the ones given, that answers its wire name."""

    def __init__(
        self,
        *buses: UseFramework,
        receipts: KeyValueStore | None = None,
        keep_for: timedelta = timedelta(days=1),
    ) -> None:
        """`receipts` stores which bus handled which event, for `keep_for` — longer than a
        redelivery can take — so a redelivery runs again only the buses that failed. Without
        one, every bus that answers the event runs again."""
        self.dispatch = EventDispatch(list(buses), receipts, retain_for=keep_for)

    @property
    def buses(self) -> list[UseFramework]:
        """The buses handed in, in order."""
        return self.dispatch.buses

    def listeners(self, name: str) -> list[UseFramework]:
        """The buses that answer this event name, in the order they were given."""
        return self.dispatch.listeners(name)

    def event_type(self, name: str) -> type[DomainEvent] | None:
        """The class an event of that name rebuilds as, or `None` when no bus knows it.

        Subscriber(planning, assurance).event_type("billing.InvoicePaid")
            ->  planning's InvoicePaid    # the first bus that registered the name
        subscriber.event_type("billing.Unknown")         ->  None
        """
        return self.dispatch.event_type(name)

    def as_known_by(self, bus: UseFramework, event: DomainEvent) -> DomainEvent:
        """This event as *that* bus declared it — its own class for the same wire name; left
        alone when the bus declared the very class that was published."""
        return self.dispatch.as_known_by(bus, event)

    def handle(self, event: DomainEvent) -> list[Any]:
        """The event to every bus that answers it, now — but the ones a receipt says already
        did; what each one returned, in order — or, once every bus ran, what failed: the error
        when one did, an `ExceptionGroup` when several did.

            Subscriber(planning, assurance).handle(InvoicePaid(…))
                ->  [<planning's answer>, <assurance's answer>]
            # both raised  ->  raises ExceptionGroup("billing.InvoicePaid failed in 2 buses")
        """
        return self.dispatch.handle(event)

    def get_async_subscriber(self) -> AsyncSubscriber:
        """The same subscriber for an `async def` caller."""
        return AsyncSubscriber(self)
