"""`EventPublication`: puts a message into a queue — what `Publisher` and `AsyncPublisher` delegate
to.

    publication = EventPublication(queue)
    publication.put(event)                         →  None: launch and forget
    publication.put(event, ResponseDTO)            →  ResponseDTO: the subscriber's answer
    await publication.aput(event, ResponseDTO)

The typed form is the same promise `UseFramework.__call__` makes for a command: the caller
names the type it expects and gets it back typed. It only holds where somebody can answer in
the same call — a `SyncQueue` with exactly one bus for that event. A `BackgroundQueue` cannot
answer from another process, and two buses have no one answer, so both are refused rather than
guessed.

**Any message.** A `DomainEvent` is what this framework recommends: published inside an
execution it goes out caused by it and in its flow (`causation_id`, `correlation_id`), unless it
already says otherwise — as a copy; the one handed in is untouched. A message of the project's own
goes into the queue as it is, and what the queue can carry is the queue's to say.

**Forwarded, when the publisher forwards.** The message goes to its `Forwarding` instead of the
queue, chained here, where the execution is: the publish returns at once with a `Future` of how it
went, so it can promise no answer.
"""

import dataclasses
from typing import Any

from sincpro_framework import exceptions
from sincpro_framework.context import context_executions
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.event_driven.domain.queue import Queue
from sincpro_framework.event_driven.services.forwarding import Forwarding


class EventPublication:
    """Puts messages into one queue — or its forwarding — and reads back the one answer a typed
    publish promised."""

    def __init__(self, queue: Queue, forwarding: bool | Forwarding = False) -> None:
        """Publish into `queue` — which one decides whether an answer can come back — or hand
        each message to a forwarding bound to it: the defaults for `True`, the one given."""
        self.queue = queue
        self.forwarding: Forwarding | None = None
        if forwarding is True:
            self.forwarding = Forwarding().bound_to(queue)
        elif isinstance(forwarding, Forwarding):
            self.forwarding = forwarding.bound_to(queue)

    def _chained(self, message: Any) -> Any:
        """The message as it goes out: a `DomainEvent` as a copy caused by the execution in play
        and in its flow; anything else as it is.

            publication._chained(InvoicePaid(…))      ->  InvoicePaid(…, causation_id="<run>")
            publication._chained({"ticket": "T-1"})   ->  {"ticket": "T-1"}
        """
        if not isinstance(message, DomainEvent):
            return message
        return dataclasses.replace(message, **context_executions.chain_for(message))

    def _one_answer(self, message: Any, answers: Any) -> Any:
        """The single answer a typed publish promised, or the reason there is none."""
        if not isinstance(answers, list):
            raise exceptions.ProgrammingError(
                f"{type(self.queue).__name__} cannot answer in the same call; "
                "publish(event) without a return type"
            )
        if len(answers) != 1:
            raise exceptions.ProgrammingError(
                f"{getattr(message, 'name', type(message).__name__)} was answered by "
                f"{len(answers)} subscribers; a typed publish needs exactly one"
            )
        return answers[0]

    def _forwarded(self, forwarding: Forwarding, message: Any, return_type: Any) -> Any:
        """The message to the forwarding, chained here — refused with a return type, since a
        forwarded message answers later or never."""
        if return_type is not None:
            raise exceptions.ProgrammingError(
                f"a forwarded publish cannot wait for {getattr(return_type, '__name__', return_type)}"
                ": it returns before the message went out — publish without a return type, or "
                "through a publisher that does not forward"
            )
        return forwarding.forward(self._chained(message))

    def put(self, message: Any, return_type: Any = None) -> Any:
        """The message into the queue; and, when a type is named, the one answer back.

        in      DatasetRegistered(...)                  →  None
        in      DatasetRegistered(...), ResponsePlan    →  ResponsePlan(...)
        in      DatasetRegistered(...), forwarded       →  Future[Forwarded], at once
        """
        if self.forwarding is not None:
            return self._forwarded(self.forwarding, message, return_type)
        answers = self.queue.put(self._chained(message))
        return None if return_type is None else self._one_answer(message, answers)

    async def aput(self, message: Any, return_type: Any = None) -> Any:
        """`put`, awaited on the queue's `aput` — or handed to the forwarding, which never
        waits."""
        if self.forwarding is not None:
            return self._forwarded(self.forwarding, message, return_type)
        answers = await self.queue.aput(self._chained(message))
        return None if return_type is None else self._one_answer(message, answers)
