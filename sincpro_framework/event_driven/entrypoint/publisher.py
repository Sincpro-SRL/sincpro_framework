"""Emits events to a queue, with the signature of a bus.

    publisher = Publisher(queue)
    publisher.publish(event)                       →  None: launch and forget
    publisher.publish(event, ResponseDTO)          →  ResponseDTO: wait for the subscriber's answer
    await publisher.get_async_publisher().publish(event, ResponseDTO)

The work is `EventPublication`'s: refusing what is not a fact, chaining the event to the
execution in play, and the one answer a typed publish promised.
"""

from typing import Any, TypeVar, overload

from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.event_driven.domain.queue import Queue
from sincpro_framework.event_driven.services.publication import EventPublication

Response = TypeVar("Response")


class AsyncPublisher:
    """`Publisher` for an `async def` caller — `aput` instead of `put`, the same rules."""

    def __init__(self, queue: Queue) -> None:
        """Publish into `queue`."""
        self.queue = queue
        self._publication = EventPublication(queue)

    @overload
    async def publish(self, event: DomainEvent) -> None: ...

    @overload
    async def publish(self, event: DomainEvent, return_type: type[Response]) -> Response: ...

    async def publish(self, event: DomainEvent, return_type: Any = None) -> Any:
        """The event into the queue, awaited; and, when a type is named, the one answer back
        — as `Publisher.publish`."""
        return await self._publication.aput(event, return_type)


class Publisher:
    """Puts facts into a queue — what a Feature holds to tell whoever listens."""

    def __init__(self, queue: Queue) -> None:
        """Publish into `queue` — which one decides whether an answer can come back."""
        self.queue = queue
        self._publication = EventPublication(queue)

    @overload
    def publish(self, event: DomainEvent) -> None: ...

    @overload
    def publish(self, event: DomainEvent, return_type: type[Response]) -> Response: ...

    def publish(self, event: DomainEvent, return_type: Any = None) -> Any:
        """The event into the queue; and, when a type is named, the one answer back.

        in      DatasetRegistered(...)                  →  None
        in      DatasetRegistered(...), ResponsePlan    →  ResponsePlan(...)

        **A queue carries facts, not orders.** A command is something one bus is asked to do,
        and it is asked directly — `bus(CommandStartRun(...))`. What goes out here already
        happened, which is why everybody who hears it may react and nobody has to.
        """
        return self._publication.put(event, return_type)

    def get_async_publisher(self) -> "AsyncPublisher":
        """The same publisher for an `async def` caller."""
        return AsyncPublisher(self.queue)
