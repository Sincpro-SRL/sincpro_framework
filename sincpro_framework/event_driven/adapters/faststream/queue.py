"""`FastStreamQueue`: `publish(event)` onto any FastStream broker — Kafka, RabbitMQ, Redis, NATS.

    queue = FastStreamQueue(KafkaBroker("kafka:9092"), options_of=keyed_by_entity).start()
    Publisher(queue).publish(RunFailed(...))           # synchronous code: the queue's own loop
    await AsyncPublisher(queue).publish(RunFailed(...))  # async code

Context: FastStream is asyncio and a broker's connection belongs to one event loop. `start()`
gives the queue a loop of its own in a daemon thread and connects the broker there, so `put`
works from any synchronous caller and waits until the broker took the message — an error
surfaces where it was published. Never started, the queue is for a service that is already
async and connected the broker itself: `aput` publishes on the caller's loop.

What travels is the event as JSON, on the channel named after it, with its envelope
(`cloud_event_headers`: its id, wire name, chain, the publishing trace and the publishing
execution's context) as headers — read where the event is published, before it is handed to the
queue's loop. `source` names this service in the envelope, what a consumer's `producers=` checks.
Topics, partitions, exchanges, retries and acks are the broker's configuration, not this queue's.
"""

import asyncio
import threading
from collections.abc import Callable, Coroutine
from typing import Any

from sincpro_framework import exceptions
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.event_driven.adapters.envelope import cloud_event_headers
from sincpro_framework.event_driven.domain.broker import Broker

type ChannelOf = Callable[[DomainEvent], str]
type OptionsOf = Callable[[DomainEvent], dict[str, Any]]


def by_event_name(event: DomainEvent) -> str:
    """The default channel: the event's wire name — `billing.InvoicePaid` on its own topic."""
    return event.name


def keyed_by_entity(event: DomainEvent) -> dict[str, Any]:
    """Kafka: the event's entity as the message key, so one entity's events stay in order."""
    return {"key": event.entity_id.encode()} if event.entity_id else {}


class FastStreamQueue:
    """A `Queue` publishing onto a FastStream broker, from synchronous or async code."""

    def __init__(
        self,
        broker: Broker,
        channel_of: ChannelOf = by_event_name,
        options_of: OptionsOf | None = None,
        source: str = "",
    ) -> None:
        """`channel_of` names where an event goes — its wire name by default; `options_of` adds
        what one broker takes and another does not, such as Kafka's `key` — nothing by
        default; `source` is this service in the envelope.

            FastStreamQueue(KafkaBroker("localhost:9092"), options_of=keyed_by_entity,
                            source="billing")
                ->  InvoicePaid on topic "billing.InvoicePaid", keyed by its entity
            FastStreamQueue(broker, channel_of=lambda event: "billing-events")
                ->  every event on one channel, told apart by its `sincpro-event` header

        Nothing connects here: `start()` gives the queue its loop and connects the broker.
        """
        self.broker = broker
        self.source = source
        self.channel_of = channel_of
        self.options_of = options_of
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    def _on_own_loop[T](
        self, work: Callable[[], Coroutine[Any, Any, T]], timeout: float | None
    ) -> T:
        """Run `work` on the queue's own loop from this thread, and wait up to `timeout` for
        its answer — a `ProgrammingError` when the queue was never started."""
        loop = self._loop
        if loop is None:
            raise exceptions.ProgrammingError(
                "this FastStreamQueue was never started: call start() where the process begins "
                "and stop() where it ends, so synchronous code has a loop to publish on"
            )
        return asyncio.run_coroutine_threadsafe(work(), loop).result(timeout)

    def start(self, timeout: float | None = 30.0) -> "FastStreamQueue":
        """A loop of the queue's own, in a daemon thread, with the broker connected on it.

            queue = FastStreamQueue(broker).start()       ->  queue, connected
            FastStreamQueue(unreachable).start(timeout=5)
                ->  raises TimeoutError after 5 s waiting for the broker to connect

        Context: the loop is the queue's before the broker connects on it — a connection
        that fails leaves that loop running, held by the queue.
        """
        loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=loop.run_forever, name="faststream-queue", daemon=True
        )
        self._thread.start()
        self._loop = loop
        self._on_own_loop(self.broker.start, timeout)
        return self

    def stop(self, timeout: float | None = 10.0) -> None:
        """Disconnect the broker and end the loop — what was published has been sent.

            queue.stop()   ->  None; broker disconnected, thread joined, loop closed
            queue.stop()   # again, or never started  ->  None, nothing to do

        1. Never started, or stopped already: nothing to do.
        2. The broker stopped on the queue's loop, waiting up to `timeout`.
        3. The loop told to stop, its thread joined up to `timeout`, the loop closed.
        Final: the queue forgets both — `start()` may begin again.
        """
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
        return cloud_event_headers(event, source=self.source)

    async def _publish(self, event: DomainEvent, headers: dict[str, str]) -> None:
        """Publish the event's JSON on its channel, with `headers` and the broker's options.

        Context: the event is written and its channel chosen here, before the broker is
        reached — what fails after that is the broker not taking it, `ServiceUnavailableError`:
        retry later."""
        written = event.as_json().encode()
        channel = self.channel_of(event)
        options = self.options_of(event) if self.options_of else {}
        try:
            await self.broker.publish(written, channel, headers=headers, **options)
        except Exception as error:
            raise exceptions.ServiceUnavailableError(
                f"the broker did not take {event.name} on {channel}: "
                f"{type(error).__name__}: {error}"
            ) from error

    def put(self, event: DomainEvent) -> None:
        """Publish from synchronous code and wait until the broker took it."""
        headers = self._headers(event)
        self._on_own_loop(lambda: self._publish(event, headers), None)

    async def aput(self, event: DomainEvent) -> None:
        """Publish from async code — on the queue's loop when it was started, else on this
        one.

            await queue.aput(InvoicePaid(…))   # started: sent from the queue's own loop
            await queue.aput(InvoicePaid(…))   # never started: sent on the caller's loop,
                                               # the broker connected by the caller

        Context: the headers are read here, on the caller's side, where the trace and the
        execution's context are — the queue's thread has neither.
        """
        headers = self._headers(event)
        if self._loop is None:
            await self._publish(event, headers)
            return
        future = asyncio.run_coroutine_threadsafe(self._publish(event, headers), self._loop)
        await asyncio.wrap_future(future)
