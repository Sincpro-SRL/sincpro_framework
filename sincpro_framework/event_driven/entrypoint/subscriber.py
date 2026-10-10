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
idempotent.
"""

import json
from typing import Any

from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.use_bus import UseFramework


class Subscriber:

    def __init__(self, *buses: UseFramework) -> None:
        self.buses: list[UseFramework] = list(buses)

    def listeners(self, name: str) -> list[UseFramework]:
        """The buses that answer this event name, in the order they were given."""
        return [bus for bus in self.buses if name in bus.dto_registry]

    def event_type(self, name: str) -> type[DomainEvent] | None:
        """The class an event of that name rebuilds as, or `None` when no bus knows it."""
        for bus in self.buses:
            found = bus.dto_registry.get(name)
            if found is not None and issubclass(found, DomainEvent):
                return found
        return None

    def handle(self, event: DomainEvent) -> list[Any]:
        """The event to every bus that answers it, now; what each one returned, in order — or,
        once every bus ran, what failed: the error when one did, an `ExceptionGroup` when
        several did."""
        answers: list[Any] = []
        failures: list[Exception] = []
        for bus in self.listeners(event.name):
            try:
                answers.append(bus(self.as_known_by(bus, event)))
            except Exception as error:  # noqa: BLE001 - the other buses still hear it
                failures.append(error)
        if len(failures) == 1:
            raise failures[0]
        if failures:
            raise ExceptionGroup(f"{event.name} failed in {len(failures)} buses", failures)
        return answers

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

    def get_async_subscriber(self) -> "AsyncSubscriber":
        """The same subscriber for an `async def` caller."""
        return AsyncSubscriber(self)


class AsyncSubscriber:

    def __init__(self, subscriber: Subscriber) -> None:
        self._subscriber = subscriber

    async def handle(self, event: DomainEvent) -> list[Any]:
        """The event to every bus that answers it, each through its async facade — or, once
        every bus ran, what failed: the error when one did, an `ExceptionGroup` when several
        did."""
        answers: list[Any] = []
        failures: list[Exception] = []
        for bus in self._subscriber.listeners(event.name):
            known = self._subscriber.as_known_by(bus, event)
            try:
                answers.append(await bus.get_async_bus()(known))
            except Exception as error:  # noqa: BLE001 - the other buses still hear it
                failures.append(error)
        if len(failures) == 1:
            raise failures[0]
        if failures:
            raise ExceptionGroup(f"{event.name} failed in {len(failures)} buses", failures)
        return answers
