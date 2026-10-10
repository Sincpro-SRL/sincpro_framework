"""Who hears an outcome: every listener of the process, and each bus's own."""

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sincpro_framework.ddd.events import DomainEvent

type OutcomeListener = Callable[[Any], Any]


class OutcomeListeners:
    """Who hears one outcome in this process: its own subscribers, and each bus's."""

    def __init__(self) -> None:
        self._process: list[OutcomeListener] = []
        self._by_bus: dict[str, list[OutcomeListener]] = {}
        self._lock = threading.Lock()

    def subscribe(self, listener: OutcomeListener, bus: str | None = None) -> OutcomeListener:
        """`listener` hears every outcome of the process — or of `bus` only. Usable as a
        decorator."""
        with self._lock:
            if bus is None:
                self._process.append(listener)
            else:
                self._by_bus.setdefault(bus, []).append(listener)
        return listener

    def unsubscribe(self, listener: OutcomeListener) -> None:
        with self._lock:
            if listener in self._process:
                self._process.remove(listener)
            for listeners in self._by_bus.values():
                if listener in listeners:
                    listeners.remove(listener)

    def heard(self, bus: str) -> bool:
        """Whether anybody hears an outcome of `bus` — read without the lock: the cost of every
        execution nobody listens to."""
        return bool(self._process) or bool(self._by_bus.get(bus))

    def listening(self, bus: str) -> list[OutcomeListener]:
        """Who hears an outcome of `bus` — each once, the process's first."""
        with self._lock:
            heard = [*self._process, *self._by_bus.get(bus, [])]
        unique: list[OutcomeListener] = []
        for listener in heard:
            if listener not in unique:
                unique.append(listener)
        return unique

    def clear(self) -> None:
        """Nobody hears anything — what a test starts from."""
        with self._lock:
            self._process.clear()
            self._by_bus.clear()


completions = OutcomeListeners()
"""The completions of this process."""

failures = OutcomeListeners()
"""The failures of this process."""


def to_publisher(publisher: Any) -> OutcomeListener:
    """A listener that hands each outcome to `publisher` — a queue, another bus, a broker."""

    def publish(outcome: "DomainEvent") -> None:
        publisher.publish(outcome)

    publish.__qualname__ = f"publish_to({type(publisher).__name__})"
    return publish
