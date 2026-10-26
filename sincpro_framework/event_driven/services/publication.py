"""`EventPublication`: puts a fact into a queue — what `Publisher` and `AsyncPublisher` delegate
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

An event published inside an execution goes out caused by it and in its flow (`causation_id`,
`correlation_id`), unless it already says otherwise — as a copy; the one handed in is untouched.
"""

import dataclasses
from typing import Any

from sincpro_framework import exceptions
from sincpro_framework.context import context_executions
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.event_driven.domain.queue import Queue


class EventPublication:
    """Puts facts into one queue, refusing orders, and reads back the one answer a typed
    publish promised."""

    def __init__(self, queue: Queue) -> None:
        """Publish into `queue` — which one decides whether an answer can come back."""
        self.queue = queue

    def _chained(self, event: Any) -> DomainEvent:
        """The event as it goes out: a copy caused by the execution in play and in its flow —
        refused, by name, when it is not a fact.

            publication._chained(InvoicePaid(…))     ->  InvoicePaid(…, causation_id="<run>")
            publication._chained(CommandStartRun(…))
                ->  raises ProgrammingError("CommandStartRun is not a DomainEvent, …")

        Context: a `DataTransferObject` handed here used to fail deep inside the queue with
        `'CommandStartRun' object has no attribute 'name'` — true, and no help at all about why
        a command has no business being published.
        """
        if not isinstance(event, DomainEvent):
            raise exceptions.ProgrammingError(
                f"{type(event).__name__} is not a DomainEvent, and a queue carries facts rather "
                "than orders: a command is asked of one bus directly, `bus(command)`. If this is "
                "meant to be a fact, make it a DomainEvent and give it a wire name"
            )
        return dataclasses.replace(event, **context_executions.chain_for(event))

    def _one_answer(self, event: DomainEvent, answers: Any) -> Any:
        """The single answer a typed publish promised, or the reason there is none."""
        if not isinstance(answers, list):
            raise exceptions.ProgrammingError(
                f"{type(self.queue).__name__} cannot answer in the same call; "
                "publish(event) without a return type"
            )
        if len(answers) != 1:
            raise exceptions.ProgrammingError(
                f"{event.name} was answered by {len(answers)} subscribers; a typed publish "
                "needs exactly one"
            )
        return answers[0]

    def put(self, event: DomainEvent, return_type: Any = None) -> Any:
        """The event into the queue; and, when a type is named, the one answer back.

        in      DatasetRegistered(...)                  →  None
        in      DatasetRegistered(...), ResponsePlan    →  ResponsePlan(...)
        """
        answers = self.queue.put(self._chained(event))
        return None if return_type is None else self._one_answer(event, answers)

    async def aput(self, event: DomainEvent, return_type: Any = None) -> Any:
        """`put`, awaited on the queue's `aput`."""
        answers = await self.queue.aput(self._chained(event))
        return None if return_type is None else self._one_answer(event, answers)
