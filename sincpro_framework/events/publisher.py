"""Emits events to a queue, with the signature of a bus.

    publisher = Publisher(queue)
    publisher.publish(event)                       →  None: launch and forget
    publisher.publish(event, ResponseDTO)          →  ResponseDTO: wait for the subscriber's answer
    await publisher.get_async_publisher().publish(event, ResponseDTO)

The typed form is the same promise `UseFramework.__call__` makes for a command: the caller
names the type it expects and gets it back typed. It only holds where somebody can answer in
the same call — a `SyncQueue` with exactly one bus for that event. A `BackgroundQueue` cannot
answer from another process, and two buses have no one answer, so both are refused rather than
guessed.
"""

from typing import Any, TypeVar, overload

from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.events.queue import Queue

Response = TypeVar("Response")


def _one_answer(queue: Queue, event: DomainEvent, answers: Any) -> Any:
    """The single answer a typed publish promised, or the reason there is none."""
    if not isinstance(answers, list):
        raise ContractViolation(
            f"{type(queue).__name__} cannot answer in the same call; "
            "publish(event) without a return type"
        )
    if len(answers) != 1:
        raise ContractViolation(
            f"{event.name} was answered by {len(answers)} subscribers; a typed publish "
            "needs exactly one"
        )
    return answers[0]


class Publisher:

    def __init__(self, queue: Queue) -> None:
        self.queue = queue

    @overload
    def publish(self, event: DomainEvent) -> None: ...

    @overload
    def publish(self, event: DomainEvent, return_type: type[Response]) -> Response: ...

    def publish(self, event: DomainEvent, return_type: Any = None) -> Any:
        """The event into the queue; and, when a type is named, the one answer back.

        in      DatasetRegistered(...)                  →  None
        in      DatasetRegistered(...), ResponsePlan    →  ResponsePlan(...)
        """
        answers = self.queue.put(event)
        return None if return_type is None else _one_answer(self.queue, event, answers)

    def get_async_publisher(self) -> "AsyncPublisher":
        """The same publisher for an `async def` caller."""
        return AsyncPublisher(self.queue)


class AsyncPublisher:

    def __init__(self, queue: Queue) -> None:
        self.queue = queue

    @overload
    async def publish(self, event: DomainEvent) -> None: ...

    @overload
    async def publish(self, event: DomainEvent, return_type: type[Response]) -> Response: ...

    async def publish(self, event: DomainEvent, return_type: Any = None) -> Any:
        answers = await self.queue.aput(event)
        return None if return_type is None else _one_answer(self.queue, event, answers)
