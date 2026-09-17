"""The queue an event goes through, and the two the framework ships.

    queue = SyncQueue(Subscriber(planning, assurance))     # same process, synchronous
    queue = BackgroundQueue(build_subscriber).start()          # another process consumes

`SyncQueue` hands the event to its subscriber where `put` was called and answers what the
buses returned. `BackgroundQueue` puts the event in a `multiprocessing.Queue`; a worker process it
started builds its own subscriber — a bus does not cross a process — and consumes. Kafka,
RabbitMQ or Redis are one more queue each, with this same surface.
"""

import multiprocessing
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.events.subscriber import Subscriber
from sincpro_framework.observability.api import process
from sincpro_framework.sincpro_logger import logger

STOP = ("__stop__", {})


@runtime_checkable
class Queue(Protocol):
    """What a publisher puts into. `put` answers what the subscriber returned when it can —
    a local queue — and `None` when the answer is somewhere else."""

    def put(self, event: DomainEvent) -> list[Any] | None: ...

    async def aput(self, event: DomainEvent) -> list[Any] | None: ...


class SyncQueue:

    def __init__(self, subscriber: Subscriber) -> None:
        self.subscriber = subscriber

    def put(self, event: DomainEvent) -> list[Any]:
        return self.subscriber.handle(event)

    async def aput(self, event: DomainEvent) -> list[Any]:
        return await self.subscriber.get_async_subscriber().handle(event)


def _consume(inbox: Any, build_subscriber: Callable[[], Subscriber]) -> None:
    """The worker: its own subscriber, one event at a time, until told to stop."""
    subscriber = build_subscriber()
    while True:
        name, payload = inbox.get()
        if (name, payload) == STOP:
            return
        event_type = subscriber.event_type(name)
        if event_type is None:
            continue
        try:
            subscriber.handle(event_type.model_validate(payload))
        except Exception as error:  # noqa: BLE001 - one event failing must not end the worker
            logger.error(f"event {name} failed in the background worker: {error!r}")
            process.record_error(error, layer="events")


class BackgroundQueue:
    """Another process consumes: `start()` spawns it with the subscriber `build_subscriber`
    answers, `put` hands the event over and returns, `stop()` lets it finish and waits.

        def build_subscriber() -> Subscriber:      # a module-level function: it runs in the child
            return Subscriber(planning, assurance)

        queue = BackgroundQueue(build_subscriber).start()
        Publisher(queue).publish(event)
        queue.stop()
    """

    def __init__(
        self, build_subscriber: Callable[[], Subscriber], context: str = "spawn"
    ) -> None:
        """`spawn` and never `fork`: a spawned worker starts a fresh interpreter, so what
        `build_subscriber()` builds — engines, pools, sessions — is its own. A forked one
        would inherit the parent's open connections, which SQLAlchemy forbids sharing."""
        if context == "fork":
            raise ContractViolation(
                "BackgroundQueue does not fork: the worker would inherit the parent's database "
                "connections; use 'spawn' or 'forkserver'"
            )
        self.build_subscriber = build_subscriber
        self._context: Any = multiprocessing.get_context(context)
        self.inbox: Any = self._context.Queue()
        self.process: Any = None

    def start(self) -> "BackgroundQueue":
        self.process = self._context.Process(
            target=_consume, args=(self.inbox, self.build_subscriber), daemon=True
        )
        self.process.start()
        return self

    def put(self, event: DomainEvent) -> None:
        self.inbox.put((event.name, event.model_dump(mode="json")))

    async def aput(self, event: DomainEvent) -> None:
        self.put(event)

    def stop(self, timeout: float | None = 10.0) -> None:
        if self.process is None:
            return
        self.inbox.put(STOP)
        self.process.join(timeout)
        if self.process.is_alive():
            self.process.terminate()
            self.process.join(1)
        self.process = None
