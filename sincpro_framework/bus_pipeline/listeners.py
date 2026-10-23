"""Who hears what an execution announces: a bus's own listeners, and the process's.

Each kind of fact — a use case that begins, one that answered, a failure that escaped — has its own
`Listeners` on every bus, and one for the process that every bus of it also tells:

    billing.on_completion(audit)                    this bus
    completions.subscribe(audit)                    every bus of the process

**A listener observes.** It is a function, called in the thread of the execution, in the order it was
added. One that raises is logged and never reaches the execution nor the next listener: an execution
neither waits for its listeners nor knows whether they ran. Nothing here is retried or kept.
"""

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from sincpro_framework.sincpro_logger import logger

if TYPE_CHECKING:
    from sincpro_framework.ddd.events import DomainEvent

type Listener = Callable[[Any], Any]


class Listeners:
    """The listeners of one kind of fact: the ones added here, and the process's."""

    def __init__(self, process: "Listeners | None" = None) -> None:
        self._own: list[Listener] = []
        self._process = process
        self._lock = threading.Lock()

    def subscribe(self, listener: Listener) -> Listener:
        """`listener` hears from now on. Usable as a decorator."""
        with self._lock:
            self._own.append(listener)
        return listener

    def unsubscribe(self, listener: Listener) -> None:
        with self._lock:
            if listener in self._own:
                self._own.remove(listener)

    def clear(self) -> None:
        """Nobody added here hears anything — what a test starts from."""
        with self._lock:
            self._own.clear()

    def heard(self) -> bool:
        """Whether anybody hears — read without the lock: it is the cost of every execution nobody
        listens to, so nothing is built for them."""
        return bool(self._own) or (self._process is not None and self._process.heard())

    def listening(self) -> list[Listener]:
        """Who hears: the process's first, then the ones added here — each once."""
        with self._lock:
            own = list(self._own)
        heard = [*(self._process.listening() if self._process is not None else []), *own]
        unique: list[Listener] = []
        for listener in heard:
            if listener not in unique:
                unique.append(listener)
        return unique

    def announce(self, event: "DomainEvent") -> None:
        """`event` to each listener, in turn, never raising."""
        for listener in self.listening():
            try:
                listener(event)
            except Exception:  # noqa: BLE001 - a listener never reaches the execution
                logger.exception(
                    f"a listener of {event.name} failed: "
                    f"{getattr(listener, '__qualname__', listener)!r}"
                )


def to_publisher(publisher: Any) -> Listener:
    """A listener that hands each event to `publisher` — a queue, another bus, a broker."""

    def publish(event: "DomainEvent") -> None:
        publisher.publish(event)

    publish.__qualname__ = f"publish_to({type(publisher).__name__})"
    return publish
