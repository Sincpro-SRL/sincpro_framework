"""`Subscribes`: what a queue in this process, or in a process it spawned, hands an event to —
the port every queue that runs the buses itself depends on, and `Subscriber` answers.

    SyncQueue(Subscriber(planning, assurance))         a Subscriber is one
    BackgroundQueue(build_subscriber)                  so is what `build_subscriber()` answers

A queue knows the subscriber only by what it asks of it: the class an event of a name rebuilds
as, and the event handed on — now, or awaited.
"""

from typing import Any, Protocol

from sincpro_framework.ddd.events import DomainEvent


class AsyncSubscribes(Protocol):
    """What an `async def` queue awaits: the event handed to the buses."""

    async def handle(self, event: DomainEvent) -> list[Any]:
        """The event to every bus that answers it — what each one answered."""
        ...


class Subscribes(Protocol):
    """What a queue hands an event to."""

    def event_type(self, name: str) -> type[DomainEvent] | None:
        """The class an event of that wire name rebuilds as, or `None` when nobody hears it."""
        ...

    def handle(self, event: DomainEvent) -> list[Any]:
        """The event to every bus that answers it — what each one answered."""
        ...

    def get_async_subscriber(self) -> AsyncSubscribes:
        """The same subscriber for an `async def` caller."""
        ...
