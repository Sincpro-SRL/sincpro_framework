"""`FastStreamQueue`: `publish(event)` onto any FastStream broker — Kafka, RabbitMQ, Redis, NATS.

    queue = FastStreamQueue(KafkaBroker("kafka:9092"), options_of=keyed_by_entity).start()
    Publisher(queue).publish(RunFailed(...))           # synchronous code: the queue's own loop
    await AsyncPublisher(queue).publish(RunFailed(...))  # async code

Context: FastStream is asyncio and a broker's connection belongs to one event loop. `start()`
gives the queue a loop of its own in a daemon thread and connects the broker there, so `put`
works from any synchronous caller and waits until the broker took the message — an error
surfaces where it was published. Never started, the queue is for a service that is already
async and connected the broker itself: `aput` publishes on the caller's loop.

What travels is the event as JSON, on the channel named after it, with the event's wire name,
the publishing trace and the publishing execution's context (`sincpro-context`: caused by it, in
its flow) as headers — read where the event is published, before it is handed to the queue's loop.
Topics, partitions, exchanges, retries and acks are the broker's configuration, not this queue's.
"""

import asyncio
import threading
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, Protocol

from sincpro_framework.context.adapters.propagation import inject
from sincpro_framework.context.infrastructure.tree import live_context
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.observability.tracing.propagation import trace_carrier

EVENT_HEADER = "sincpro-event"
"""The event's wire name, so a channel several events share still routes each to its class."""


class Broker(Protocol):
    """What the queue and `subscribe` use of a FastStream broker — every one of them has it."""

    def publish(self, message: Any, *args: Any, **kwargs: Any) -> Awaitable[Any]: ...

    def subscriber(self, *args: Any, **kwargs: Any) -> Callable[..., Any]: ...

    def start(self) -> Coroutine[Any, Any, Any]: ...

    def stop(self) -> Coroutine[Any, Any, Any]: ...


type ChannelOf = Callable[[DomainEvent], str]
type OptionsOf = Callable[[DomainEvent], dict[str, Any]]


def by_event_name(event: DomainEvent) -> str:
    return event.name


def keyed_by_entity(event: DomainEvent) -> dict[str, Any]:
    """Kafka: the event's entity as the message key, so one entity's events stay in order."""
    return {"key": event.entity_id.encode()} if event.entity_id else {}


class FastStreamQueue:
    def __init__(
        self,
        broker: Broker,
        channel_of: ChannelOf = by_event_name,
        options_of: OptionsOf | None = None,
    ) -> None:
        """`channel_of` names where an event goes — its wire name by default; `options_of` adds
        what one broker takes and another does not, such as Kafka's `key` — nothing by
        default."""
        self.broker = broker
        self.channel_of = channel_of
        self.options_of = options_of
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    def _on_own_loop[T](
        self, work: Callable[[], Coroutine[Any, Any, T]], timeout: float | None
    ) -> T:
        loop = self._loop
        if loop is None:
            raise ContractViolation(
                "this FastStreamQueue was never started: call start() where the process begins "
                "and stop() where it ends, so synchronous code has a loop to publish on"
            )
        return asyncio.run_coroutine_threadsafe(work(), loop).result(timeout)

    def start(self, timeout: float | None = 30.0) -> "FastStreamQueue":
        """A loop of the queue's own, in a daemon thread, with the broker connected on it."""
        loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=loop.run_forever, name="faststream-queue", daemon=True
        )
        self._thread.start()
        self._loop = loop
        self._on_own_loop(self.broker.start, timeout)
        return self

    def stop(self, timeout: float | None = 10.0) -> None:
        """Disconnect the broker and end the loop — what was published has been sent."""
        if self._loop is None or self._thread is None:
            return
        self._on_own_loop(self.broker.stop, timeout)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout)
        self._loop.close()
        self._loop, self._thread = None, None

    def _headers(self, event: DomainEvent) -> dict[str, str]:
        """Read where the event is published: the queue's loop runs in a thread of its own, where
        neither the trace nor the execution's context is."""
        return {**inject(live_context()), **trace_carrier(), EVENT_HEADER: event.name}

    async def _publish(self, event: DomainEvent, headers: dict[str, str]) -> None:
        await self.broker.publish(
            event.as_json().encode(),
            self.channel_of(event),
            headers=headers,
            **(self.options_of(event) if self.options_of else {}),
        )

    def put(self, event: DomainEvent) -> None:
        """Publish from synchronous code and wait until the broker took it."""
        headers = self._headers(event)
        self._on_own_loop(lambda: self._publish(event, headers), None)

    async def aput(self, event: DomainEvent) -> None:
        """Publish from async code — on the queue's loop when it was started, else on this one."""
        headers = self._headers(event)
        if self._loop is None:
            await self._publish(event, headers)
            return
        future = asyncio.run_coroutine_threadsafe(self._publish(event, headers), self._loop)
        await asyncio.wrap_future(future)
