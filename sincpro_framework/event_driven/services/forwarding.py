"""`Forwarding`: a publisher's messages held and forwarded — store-and-forward, in the process.

    publisher = Publisher(FastStreamQueue(rabbit).start(), forwarding=True)
    future = publisher.publish(InvoicePaid(…))      returns at once
    future.result()                                 ->  Forwarded(attempts=1, delivered_at=…)

**Nobody waits.** `publish` puts the message at the end of a line in memory and returns a `Future`
of how it went. One thread of the forwarding's own sends the line in order, one message at a time
— never two at once, never one ahead of another — started by the first message, so there is
nothing to run or deploy beside the service.

**A broker that is down is waited out.** A failed attempt keeps the message first in line; the
thread sleeps — on an event, not a loop — the backoff, or the time the error asks for, and tries
the same message again. Its `CircuitBreaker`, stored where the context stores its circuits, stops
the attempts while the broker keeps failing and lets one through once it may be back; replicas
that share a store open and close together. What fails the same way on every attempt — a message
that does not fit, a refusal (`PERMANENT`) — is set aside, so it never holds the line.

**Each message in its own context.** The message is sent in the context it was published in — its
trace, its execution, its tenant — so a queue that reads its envelope where it publishes still
finds them on this thread.

**Gone with the process.** The line lives in this process's memory: a message still in it when the
process dies is not sent. A fact that must survive that is stored with the change that made it
true (event storing), and the stored event says whether it went out.
"""

import threading
from collections import deque
from collections.abc import Sequence
from concurrent.futures import Future
from contextvars import Context, copy_context
from datetime import timedelta
from typing import Any

from sincpro_framework import exceptions
from sincpro_framework.circuit_breaker import CircuitBreaker, CircuitOpen
from sincpro_framework.common.failures import PERMANENT, refined_failure_kind, retry_after
from sincpro_framework.ddd.entity import utc_now
from sincpro_framework.event_driven.domain.forwarding import Forwarded, ForwardingHook
from sincpro_framework.event_driven.domain.queue import Queue
from sincpro_framework.sincpro_logger import logger


class Forwarding:
    """Holds a publisher's messages and forwards them in order, on a thread of its own. See the
    module."""

    def __init__(
        self,
        name: str = "",
        breaker: CircuitBreaker | None = None,
        hooks: Sequence[ForwardingHook] = (),
        wait: timedelta = timedelta(seconds=1),
        longest_wait: timedelta = timedelta(minutes=1),
    ) -> None:
        """`name` names its circuit and its thread — the queue's class unless given; `breaker`
        is the circuit its attempts go through; `wait` is the first pause after a failed
        attempt, doubled each one after, never past `longest_wait`.

            Forwarding()                                     the defaults
            Forwarding("rabbit", hooks=[Audited()])
            Forwarding(breaker=CircuitBreaker("rabbit", failures=3))
            Forwarding(wait=timedelta(0))
                ->  raises ProgrammingError("a forwarding waits a positive time …")

        Nothing starts here: the first message starts the thread.
        """
        if wait <= timedelta(0) or longest_wait < wait:
            raise exceptions.ProgrammingError(
                f"a forwarding waits a positive time, at most longest_wait: wait={wait}, "
                f"longest_wait={longest_wait}"
            )
        self.name = name
        self.breaker = breaker
        self.hooks = list(hooks)
        self.wait = wait
        self.longest_wait = longest_wait
        self.queue: Queue | None = None
        self._line: deque[tuple[Any, Context, Future]] = deque()
        self._arrived = threading.Condition()
        self._pause = threading.Event()
        self._thread: threading.Thread | None = None

    def bound_to(self, queue: Queue) -> "Forwarding":
        """The queue it forwards to — one: a second publisher handing it another queue is
        refused, since one line in order cannot go to two places.

            Forwarding().bound_to(rabbit_queue)          ->  the forwarding, its circuit named
                                                             after the queue unless named
            forwarding.bound_to(kafka_queue)  # after rabbit_queue
                ->  raises ProgrammingError("this Forwarding already forwards to …")
        """
        if self.queue is not None and self.queue is not queue:
            raise exceptions.ProgrammingError(
                f"this Forwarding already forwards to {type(self.queue).__name__}: give each "
                "queue a Forwarding of its own"
            )
        self.queue = queue
        self.name = self.name or type(queue).__name__
        self.breaker = self.breaker or CircuitBreaker(f"forwarding.{self.name}")
        return self

    def _told(self, moment: str, message: Any, forwarded: Forwarded) -> None:
        """Every hook's `moment` — one that raises is logged, and the rest still told."""
        for hook in self.hooks:
            try:
                getattr(hook, moment)(message, forwarded)
            except Exception:  # noqa: BLE001 - a hook never stops the line
                logger.exception(
                    f"forwarding {self.name}: {type(hook).__name__}.{moment} failed"
                )

    def _finished(self, forwarded: Forwarded) -> None:
        """The first message off the line, its `Future` answered with how it went."""
        with self._arrived:
            _message, _context, future = self._line.popleft()
        future.set_result(forwarded)

    def _failed(self, message: Any, error: Exception, attempts: int) -> timedelta | None:
        """An attempt that raised: told to the hooks, and how long to wait before the next — or
        `None` when the message is set aside.

            ServiceUnavailableError, attempt 1   ->  wait
            ServiceUnavailableError, attempt 4   ->  wait × 8, never past longest_wait
            retry_after=30s, attempt 1           ->  30 s, the longer of the two
            ClientError (INVALID)                ->  None — set aside, its Future answered
        """
        kind = refined_failure_kind(error)
        reason = f"{type(error).__name__}: {error}"
        set_aside = kind in PERMANENT
        forwarded = Forwarded(attempts=attempts, last_error=reason, set_aside=set_aside)
        logger.warning(
            f"forwarding {self.name}: {getattr(message, 'name', type(message).__name__)} "
            f"failed (attempt {attempts}){', set aside' if set_aside else ''}: {reason}"
        )
        self._told("after_failure", message, forwarded)
        if set_aside:
            self._finished(forwarded)
            return None
        backoff = min(self.wait * 2 ** (attempts - 1), self.longest_wait)
        return max(backoff, retry_after(error, kind) or timedelta(0))

    def _forwarded_forever(self, queue: Queue, breaker: CircuitBreaker) -> None:
        """The thread: the first message of the line, sent through the circuit in the context it
        was published in, until it went or was set aside — then the next.

        1. Nothing in line: wait until something arrives.
        2. The circuit open: not an attempt — wait what it says, try the same message again.
        3. The attempt raised: `_failed` says how long to wait, or sets the message aside.
        Final: delivered — told, answered, and the next message's attempts count from zero.
        """
        attempts = 0
        while True:
            with self._arrived:
                while not self._line:
                    self._arrived.wait()
                message, context, _future = self._line[0]
            try:
                context.run(breaker.call, queue.put, message)
            except CircuitOpen as closed:
                self._pause.wait(closed.retry_after.total_seconds())
                continue
            except Exception as error:  # noqa: BLE001 - an attempt is decided, never raised
                attempts += 1
                pause = self._failed(message, error, attempts)
                if pause is None:
                    attempts = 0
                else:
                    self._pause.wait(pause.total_seconds())
                continue
            forwarded = Forwarded(attempts=attempts + 1, delivered_at=utc_now())
            self._told("after_delivery", message, forwarded)
            self._finished(forwarded)
            attempts = 0

    def forward(self, message: Any) -> "Future[Forwarded]":
        """`message` at the end of the line, with the context it is published in — the thread
        started if it is not running. Returns at once.

            forwarding.forward(InvoicePaid(…))   ->  Future, answered with a `Forwarded`
            forwarding.forward(event)  # never bound
                ->  raises ProgrammingError("this Forwarding forwards to no queue …")
        """
        if self.queue is None or self.breaker is None:
            raise exceptions.ProgrammingError(
                "this Forwarding forwards to no queue: hand it to a Publisher — "
                "Publisher(queue, forwarding=forwarding)"
            )
        future: Future[Forwarded] = Future()
        with self._arrived:
            self._line.append((message, copy_context(), future))
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._forwarded_forever,
                    args=(self.queue, self.breaker),
                    name=f"forwarding-{self.name}",
                    daemon=True,
                )
                self._thread.start()
            self._arrived.notify()
        return future
