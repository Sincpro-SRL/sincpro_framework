"""`BackgroundQueue`: another process consumes — a `multiprocessing.Queue` and a worker it
spawned, which builds its own subscriber because a bus does not cross a process.

    queue = BackgroundQueue(build_subscriber).start()

**Not durable.** What is in the queue when the process dies is gone. A fact that must survive a
crash is stored by the repository in the transaction of the change — its event table says whether
it went out.
"""

import json
import multiprocessing
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from sincpro_framework import exceptions
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.ddd.events.domain_event import NAME
from sincpro_framework.event_driven.domain.subscribes import Subscribes
from sincpro_framework.observability import process, trace_carrier, within_trace
from sincpro_framework.sincpro_logger import logger

STOP = ("", {})
"""What `stop()` puts in so the worker returns. The same two parts every event travels in — its
JSON and the trace — so the consumer unpacks one shape and nothing else."""


def _consume(inbox: Any, build_subscriber: Callable[[], Subscribes]) -> None:
    """The worker: its own subscriber, one message at a time, until told to stop."""
    subscriber = build_subscriber()
    while True:
        raw, carrier = inbox.get()
        if (raw, carrier) == STOP:
            return
        written = json.loads(raw)
        name = written.get(NAME, "")
        event_type = subscriber.event_type(name)
        if event_type is None:
            logger.debug(f"event {name} {written.get('id')}: no bus of the worker answers it")
            continue
        try:
            event = event_type.from_json(written)
        except ValidationError as error:
            logger.error(
                f"event {name} {written.get('id')} does not fit {event_type.__name__} in "
                f"the background worker and is not delivered: {error}"
            )
            process.record_error(error, layer="events")
            continue
        try:
            with within_trace(carrier):
                subscriber.handle(event)
        except Exception as error:  # noqa: BLE001 - one event failing must not end the worker
            if not process.was_reported(error):
                logger.exception(f"event {name} failed in the background worker")
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
        self, build_subscriber: Callable[[], Subscribes], context: str = "spawn"
    ) -> None:
        """`spawn` and never `fork`: a spawned worker starts a fresh interpreter, so what
        `build_subscriber()` builds — engines, pools, sessions — is its own. A forked one
        would inherit the parent's open connections, which SQLAlchemy forbids sharing.

            BackgroundQueue(build_subscriber)      ->  spawn, an empty inbox, no worker
            BackgroundQueue(build_subscriber, "forkserver")  ->  accepted
            BackgroundQueue(build_subscriber, "fork")
                ->  raises ProgrammingError("BackgroundQueue does not fork: …")
            BackgroundQueue(Subscriber(planning))
                ->  raises ProgrammingError("… Subscriber is not callable …")

        Nothing is spawned here — `start()` does it.
        """
        if context == "fork":
            raise exceptions.ProgrammingError(
                "BackgroundQueue does not fork: the worker would inherit the parent's database "
                "connections; use 'spawn' or 'forkserver'"
            )
        if not callable(build_subscriber):
            raise exceptions.ProgrammingError(
                f"BackgroundQueue takes a function that builds a Subscriber; "
                f"{type(build_subscriber).__name__} is not callable. The worker is another "
                "interpreter, and a bus cannot be sent to it — it holds context variables that "
                "cannot be pickled. Hand over a module-level function and it will build its own "
                "there"
            )
        self.build_subscriber = build_subscriber
        self._context: Any = multiprocessing.get_context(context)
        self.inbox: Any = self._context.Queue()
        self.process: Any = None

    def start(self) -> "BackgroundQueue":
        """Spawns the worker — once: a second worker on the same inbox would leave the first
        without a `stop()` that reaches it.

            queue = BackgroundQueue(build_subscriber).start()   ->  queue, its worker alive
            queue.start()
                ->  raises ProgrammingError("this BackgroundQueue was already started: …")
            queue.stop(); queue.start()                         ->  a new worker, same inbox

        The worker is a daemon: it never outlives the process that spawned it.
        """
        if self.process is not None:
            raise exceptions.ProgrammingError(
                "this BackgroundQueue was already started: call stop() before starting it again"
            )
        self.process = self._context.Process(
            target=_consume, args=(self.inbox, self.build_subscriber), daemon=True
        )
        self.process.start()
        return self

    def put(self, event: DomainEvent) -> None:
        """The event as its own JSON (`DomainEvent.as_json`, its `name` included), and the trace
        it was published under, over to the worker.

        **Refused when nobody is on the other end** — never started, or its worker died. The
        inbox accepts whatever it is handed whether or not a worker reads it, so a fact that was
        supposed to leave the process would wait for nobody and say nothing; a refusal says
        it where it was published.

            queue.put(InvoicePaid(…))    ->  None — ('{"name": "billing.InvoicePaid", …}',
                                             {"traceparent": "00-…"}) in the inbox
            # never started  ->  raises ProgrammingError("… never started: call start() …")
            # worker died    ->  raises ProgrammingError("… (exit code 1): …")

        It returns once the event is in the inbox — never when the worker handled it.
        """
        if self.process is None:
            raise exceptions.ProgrammingError(
                f"{event.name} was published to a BackgroundQueue that was never started: "
                "call start() where the process begins and stop() where it ends, or use a "
                "SyncQueue where the work is in-process"
            )
        if not self.process.is_alive():
            raise exceptions.ProgrammingError(
                f"{event.name} was published to a BackgroundQueue whose worker is no longer "
                f"running (exit code {self.process.exitcode}): it would wait in the inbox for "
                "nobody — see the worker's log, then stop() and start() it again"
            )
        self.inbox.put((event.as_json(), trace_carrier()))

    async def aput(self, event: DomainEvent) -> None:
        """`put` — handing over to the inbox does not wait for the worker."""
        self.put(event)

    def stop(self, timeout: float | None = 10.0) -> None:
        """Let the worker finish what is in the inbox, and wait for it.

        Context: the stop marker goes in behind every event already put, so a clean stop
        delivers them all; `timeout` bounds the wait for a worker stuck on one. Calling it on
        a queue never started, or already stopped, does nothing.

            queue.stop()              ->  None, every event put before it delivered
            queue.stop(timeout=2.0)   # worker stuck on one event  ->  terminated after 2 s
            queue.stop()              # again                       ->  None, nothing to do

        1. Not started: nothing to do.
        2. `STOP` is put in the inbox and the worker is joined for up to `timeout` seconds.
        3. Still alive: it is terminated, and joined one more second.
        Final: the queue forgets its worker — `start()` may spawn a new one.
        """
        if self.process is None:
            return
        self.inbox.put(STOP)
        self.process.join(timeout)
        if self.process.is_alive():
            self.process.terminate()
            self.process.join(1)
        self.process = None
