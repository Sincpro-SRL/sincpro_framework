"""`Queue`: what a publisher puts a message into — the port every carrier implements.

    SyncQueue · BackgroundQueue · FastStreamQueue · SnsQueue · SqsQueue · EventBridgeQueue

`put` answers what the subscriber returned when it can — a queue in the same process — and
`None` when the answer is somewhere else. A message is a `DomainEvent` unless a queue says it
carries more: one that writes bytes takes a `serializer` for a message of the project's own.
"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Queue(Protocol):
    """What a publisher puts into."""

    def put(self, message: Any, /) -> list[Any] | None:
        """Hand `message` on — the subscribers' answers when they ran here, else `None`."""
        ...

    async def aput(self, message: Any, /) -> list[Any] | None:
        """`put` for an event loop — what an async publisher awaits."""
        ...
