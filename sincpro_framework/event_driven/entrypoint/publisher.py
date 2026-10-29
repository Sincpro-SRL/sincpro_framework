"""Emits messages to a queue, with the signature of a bus.

    publisher = Publisher(queue)
    publisher.publish(event)                       →  None: launch and forget
    publisher.publish(event, ResponseDTO)          →  ResponseDTO: wait for the subscriber's answer
    await publisher.get_async_publisher().publish(event, ResponseDTO)

    publisher = Publisher(queue, forwarding=True)  →  held and forwarded in order on its own thread
    publisher.publish(event)                       →  Future[Forwarded], at once

The work is `EventPublication`'s: chaining a `DomainEvent` to the execution in play, handing a
message to the forwarding when there is one, and the one answer a typed publish promised. Any
message is published; a `DomainEvent` is the one this framework recommends.
"""

from concurrent.futures import Future
from typing import Any, Literal, TypeVar, overload

from sincpro_framework.event_driven.domain.forwarding import Forwarded
from sincpro_framework.event_driven.domain.queue import Queue
from sincpro_framework.event_driven.services.forwarding import Forwarding
from sincpro_framework.event_driven.services.publication import EventPublication

Response = TypeVar("Response")


class AsyncPublisher[Answer]:
    """`Publisher` for an `async def` caller — `aput` instead of `put`, the same rules."""

    @overload
    def __init__(
        self: "AsyncPublisher[None]", queue: Queue, forwarding: Literal[False] = False
    ) -> None: ...

    @overload
    def __init__(
        self: "AsyncPublisher[Future[Forwarded]]",
        queue: Queue,
        forwarding: Literal[True] | Forwarding,
    ) -> None: ...

    def __init__(self, queue: Queue, forwarding: bool | Forwarding = False) -> None:
        """Publish into `queue`, through `forwarding` when asked — as `Publisher`."""
        self.queue = queue
        self._publication = EventPublication(queue, forwarding)

    @overload
    async def publish(self, event: Any) -> Answer: ...

    @overload
    async def publish(self, event: Any, return_type: type[Response]) -> Response: ...

    async def publish(self, event: Any, return_type: Any = None) -> Any:
        """The message into the queue, awaited; and, when a type is named, the one answer back
        — as `Publisher.publish`."""
        return await self._publication.aput(event, return_type)


class Publisher[Answer]:
    """Puts messages into a queue — what a Feature holds to tell whoever listens."""

    @overload
    def __init__(
        self: "Publisher[None]", queue: Queue, forwarding: Literal[False] = False
    ) -> None: ...

    @overload
    def __init__(
        self: "Publisher[Future[Forwarded]]",
        queue: Queue,
        forwarding: Literal[True] | Forwarding,
    ) -> None: ...

    def __init__(self, queue: Queue, forwarding: bool | Forwarding = False) -> None:
        """Publish into `queue` — which one decides whether an answer can come back. With
        `forwarding`, each message is held and forwarded in order on a thread of its own: `True`
        for the defaults, a `Forwarding` to set its circuit, its waits and its hooks.

            Publisher(queue)                                    sends in the call
            Publisher(queue, forwarding=True)                   held, forwarded, never waited for
            Publisher(queue, forwarding=Forwarding("rabbit", hooks=[Audited()]))
        """
        self.queue = queue
        self._publication = EventPublication(queue, forwarding)

    @property
    def forwarding(self) -> Forwarding | None:
        """The forwarding its messages go through, or `None` when it sends in the call."""
        return self._publication.forwarding

    @overload
    def publish(self, event: Any) -> Answer: ...

    @overload
    def publish(self, event: Any, return_type: type[Response]) -> Response: ...

    def publish(self, event: Any, return_type: Any = None) -> Any:
        """The message into the queue; and, when a type is named, the one answer back.

        in      DatasetRegistered(...)                  →  None
        in      DatasetRegistered(...), ResponsePlan    →  ResponsePlan(...)
        in      DatasetRegistered(...), forwarding      →  Future[Forwarded], at once

        **A queue carries facts, not orders.** A command is something one bus is asked to do,
        and it is asked directly — `bus(CommandStartRun(...))`. What goes out here already
        happened, which is why everybody who hears it may react and nobody has to.
        """
        return self._publication.put(event, return_type)

    def get_async_publisher(self) -> "AsyncPublisher[Any]":
        """The same publisher for an `async def` caller — through the same forwarding."""
        return AsyncPublisher(self.queue, self.forwarding or False)
