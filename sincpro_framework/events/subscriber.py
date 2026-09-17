"""The buses that hear an event, handed in explicitly.

    subscriber = Subscriber(planning, assurance)
    subscriber.handle(event)                     →  what each bus that knows it answered
    await subscriber.get_async_subscriber().handle(event)

A bus declares nothing here: its registry already says which DTOs it answers, an event is a
DTO, and the subscriber executes the bus with it. `@bus.feature([SomeCommand, SomeEvent])` is
the whole subscription. The async form runs each bus through its own `get_async_bus()`, the
same way a caller that is `async def` runs a command.
"""

from typing import Any

from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.use_bus import UseFramework


def _registry_of(bus: UseFramework) -> dict[str, type]:
    """The DTO names a bus answers, building it first if nobody has yet."""
    if not bus.was_initialized:
        bus.build_root_bus()
    return getattr(bus.bus, "dto_registry", None) or {}


class Subscriber:

    def __init__(self, *buses: UseFramework) -> None:
        self.buses: list[UseFramework] = list(buses)

    def listeners(self, name: str) -> list[UseFramework]:
        """The buses that answer this event name, in the order they were given."""
        return [bus for bus in self.buses if name in _registry_of(bus)]

    def event_type(self, name: str) -> type[DomainEvent] | None:
        """The class an event of that name rebuilds as, or `None` when no bus knows it."""
        for bus in self.buses:
            found = _registry_of(bus).get(name)
            if found is not None and issubclass(found, DomainEvent):
                return found
        return None

    def handle(self, event: DomainEvent) -> list[Any]:
        """The event to every bus that answers it, now; what each one returned, in order."""
        return [bus(event) for bus in self.listeners(event.name)]

    def get_async_subscriber(self) -> "AsyncSubscriber":
        """The same subscriber for an `async def` caller."""
        return AsyncSubscriber(self)


class AsyncSubscriber:

    def __init__(self, subscriber: Subscriber) -> None:
        self._subscriber = subscriber

    async def handle(self, event: DomainEvent) -> list[Any]:
        """The event to every bus that answers it, each through its async facade."""
        answers = []
        for bus in self._subscriber.listeners(event.name):
            answers.append(await bus.get_async_bus()(event))
        return answers
