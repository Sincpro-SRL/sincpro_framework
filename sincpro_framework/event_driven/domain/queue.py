"""`Queue`: what a publisher puts an event into — the port every carrier implements.

    SyncQueue · BackgroundQueue · FastStreamQueue · RepositoryQueue · RecordingQueue

`put` answers what the subscriber returned when it can — a queue in the same process — and
`None` when the answer is somewhere else.
"""

from typing import Any, Protocol, runtime_checkable

from sincpro_framework.ddd.events import DomainEvent


@runtime_checkable
class Queue(Protocol):
    """What a publisher puts into."""

    def put(self, event: DomainEvent) -> list[Any] | None: ...

    async def aput(self, event: DomainEvent) -> list[Any] | None: ...
