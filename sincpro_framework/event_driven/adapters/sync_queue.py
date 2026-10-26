"""`SyncQueue`: the subscriber answers in the same call, in the same process.

queue = SyncQueue(Subscriber(planning, assurance))
"""

from collections.abc import Callable
from typing import Any

from sincpro_framework import exceptions
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.event_driven.domain.subscribes import Subscribes


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

    Built once, on the first publish, and held from then on.
    """

    def __init__(self, subscriber: Subscribes | Callable[[], Subscribes]) -> None:
        """Hold the subscriber, or the function that builds it — refused at once, as a
        `ProgrammingError`, when `subscriber` is neither."""
        if not hasattr(subscriber, "handle") and not callable(subscriber):
            raise exceptions.ProgrammingError(
                f"SyncQueue takes a Subscriber or a function that builds one; "
                f"{type(subscriber).__name__} is neither"
            )
        self._given = subscriber
        self._built: Subscribes | None = None if callable(subscriber) else subscriber

    @property
    def subscriber(self) -> Subscribes:
        """The subscriber, built on first use when a function was handed over."""
        if self._built is None:
            given = self._given
            self._built = given() if callable(given) else given
        return self._built

    def put(self, event: DomainEvent) -> list[Any]:
        """Handle `event` now, in this call — what each bus that listens answered."""
        return self.subscriber.handle(event)

    async def aput(self, event: DomainEvent) -> list[Any]:
        """Handle `event` now, through the subscriber's async twin — the buses run on worker
        threads, so the event loop is not blocked."""
        return await self.subscriber.get_async_subscriber().handle(event)
