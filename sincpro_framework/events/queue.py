"""The queue an event goes through, and the two the framework ships.

    queue = SyncQueue(Subscriber(planning, assurance))     # same process, synchronous
    queue = BackgroundQueue(build_subscriber).start()          # another process consumes

`SyncQueue` hands the event to its subscriber where `put` was called and answers what the
buses returned. `BackgroundQueue` puts the event in a `multiprocessing.Queue`; a worker process it
started builds its own subscriber — a bus does not cross a process — and consumes. Kafka,
RabbitMQ or Redis are one more queue each, with this same surface.
"""

import dataclasses
import multiprocessing
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Any, Protocol, runtime_checkable

from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.events.subscriber import Subscriber
from sincpro_framework.observability.api import process
from sincpro_framework.sincpro_logger import logger

STOP = ("__stop__", {}, {})
"""What `stop()` puts in so the worker returns. The same three-part envelope every event
travels in, so the consumer unpacks one shape and nothing else."""


def _carrier() -> dict[str, str]:
    """The trace that is running right now, as W3C headers — `{'traceparent': '00-…'}`, and
    empty when nothing is tracing or OpenTelemetry is not installed.

    The event itself is left alone: a trace is about the call that produced the fact, not part
    of the fact, so it rides beside the payload rather than inside `DomainEvent`.
    """
    try:
        from opentelemetry.propagate import inject
    except ImportError:
        return {}
    carrier: dict[str, str] = {}
    inject(carrier)
    return carrier


@contextmanager
def _adopted(carrier: dict[str, str]) -> Generator[None]:
    """Runs the block inside the trace the carrier names, so what the consumer does lands
    under the span that published — one trace across the process boundary instead of two
    unrelated ones. Without a carrier, or without OpenTelemetry, it is a plain block.

    The same adoption `entrypoints/rpc/entrypoint.py` does for an incoming `traceparent`
    header; this is that boundary, asynchronous.
    """
    if not carrier:
        yield
        return
    try:
        from opentelemetry import context as otel_context
        from opentelemetry.propagate import extract
    except ImportError:
        yield
        return
    token = otel_context.attach(extract(carrier))
    try:
        yield
    finally:
        otel_context.detach(token)


@runtime_checkable
class Queue(Protocol):
    """What a publisher puts into. `put` answers what the subscriber returned when it can —
    a local queue — and `None` when the answer is somewhere else."""

    def put(self, event: DomainEvent) -> list[Any] | None: ...

    async def aput(self, event: DomainEvent) -> list[Any] | None: ...


class SyncQueue:
    """The subscriber answers in the same call.

        SyncQueue(Subscriber(planning, assurance))    the buses already exist
        SyncQueue(build_subscriber)                   they do not yet

    **Both forms, for the same reason `BackgroundQueue` takes a function.** A queue needs a
    subscriber, a subscriber needs the buses, and a bus needs the publisher the queue is behind
    — wire that in one breath and it is a circle. Handing over a function instead of a
    subscriber cuts it: nothing is asked for until somebody publishes, and by then every bus
    exists. It is the same shape as `Hooks.inject`, and it is why a composition root can build
    in one direction and never import backwards.

    Built once, on the first publish, and kept.
    """

    def __init__(self, subscriber: "Subscriber | Callable[[], Subscriber]") -> None:
        if not hasattr(subscriber, "handle") and not callable(subscriber):
            raise ContractViolation(
                f"SyncQueue takes a Subscriber or a function that builds one; "
                f"{type(subscriber).__name__} is neither"
            )
        self._given = subscriber
        self._built: Subscriber | None = (
            subscriber if isinstance(subscriber, Subscriber) else None
        )

    @property
    def subscriber(self) -> Subscriber:
        """The subscriber, built on first use when a function was handed over."""
        if self._built is None:
            self._built = self._given()  # type: ignore[operator]
        return self._built

    def put(self, event: DomainEvent) -> list[Any]:
        return self.subscriber.handle(event)

    async def aput(self, event: DomainEvent) -> list[Any]:
        return await self.subscriber.get_async_subscriber().handle(event)


def _consume(inbox: Any, build_subscriber: Callable[[], Subscriber]) -> None:
    """The worker: its own subscriber, one event at a time, until told to stop."""
    subscriber = build_subscriber()
    while True:
        name, payload, carrier = inbox.get()
        if (name, payload, carrier) == STOP:
            return
        event_type = subscriber.event_type(name)
        if event_type is None:
            continue
        try:
            with _adopted(carrier):
                subscriber.handle(event_type(**payload))
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
        if isinstance(build_subscriber, Subscriber):
            raise ContractViolation(
                "BackgroundQueue takes a function that builds a Subscriber, not one already "
                "built: the worker is another interpreter, and a bus cannot be sent to it — "
                "it holds context variables that cannot be pickled. Hand over a module-level "
                "function and it will build its own there"
            )
        if not callable(build_subscriber):
            raise ContractViolation(
                f"BackgroundQueue takes a function that builds a Subscriber; "
                f"{type(build_subscriber).__name__} is not callable"
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
        """The event, and the trace it was published under, over to the worker.

        **Refused when nobody is on the other end.** The inbox accepts whatever it is handed
        whether or not a worker was ever spawned, so a process that publishes without calling
        `start()` — a CLI run, a test, a notebook — used to enqueue every event into a queue no
        one reads, and say nothing at all. A fact that was supposed to leave the process and
        did not is worse than a refusal.
        """
        if self.process is None:
            raise ContractViolation(
                f"{event.name} was published to a BackgroundQueue that was never started: "
                "call start() where the process begins and stop() where it ends, or use a "
                "SyncQueue where the work is in-process"
            )
        self.inbox.put((event.name, dataclasses.asdict(event), _carrier()))

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
