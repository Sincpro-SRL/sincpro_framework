"""What decides *when* the gateway looks at its crons. A clock never executes anything itself:
it calls `look(now)`, and the gateway decides what is due."""

import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from threading import Event
from typing import Protocol

type OnLook = Callable[[datetime], None]


class Clock(Protocol):
    def now(self) -> datetime: ...

    def run(self, look: OnLook) -> None:
        """Start calling `look`. Blocks for a clock that owns a loop; returns for one driven from
        outside."""
        ...

    def stop(self) -> None: ...


class InProcessClock:
    """Looks every `resolution`, on the thread that called `run`, until `stop` — also when
    `stop` came first: a stopped clock does not start again.

    Context: `jitter` adds up to that much to each wait, so replicas started together stop
    claiming the same tick at the same instant. A cron runs within `resolution + jitter` of its
    time — seconds, for schedules measured in minutes.
    """

    def __init__(
        self, resolution: timedelta = timedelta(seconds=5), jitter: timedelta = timedelta(0)
    ) -> None:
        self.resolution = resolution
        self.jitter = jitter
        self._stopped = Event()

    def now(self) -> datetime:
        return datetime.now(UTC)

    def interval(self) -> timedelta:
        """How long until the next look."""
        return self.resolution + self.jitter * random.random()

    def run(self, look: OnLook) -> None:
        while not self._stopped.is_set():
            look(self.now())
            self._stopped.wait(self.interval().total_seconds())

    def stop(self) -> None:
        self._stopped.set()


class ManualClock:
    """Time moves when the test says so: `advance(minutes=2)` makes every gateway that runs on it."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("ManualClock needs an aware start")
        self._now = start
        self._looks: list[OnLook] = []

    def now(self) -> datetime:
        return self._now

    def run(self, look: OnLook) -> None:
        self._looks.append(look)

    def stop(self) -> None:
        self._looks.clear()

    def advance(
        self, days: int = 0, hours: int = 0, minutes: int = 0, seconds: int = 0
    ) -> None:
        self._now += timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
        for look in list(self._looks):
            look(self._now)
