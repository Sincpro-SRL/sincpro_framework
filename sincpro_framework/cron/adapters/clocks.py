"""The clocks: `InProcessClock` looks on a loop of its own, `ManualClock` when a test says so."""

import random
from datetime import UTC, datetime, timedelta
from threading import Event

from sincpro_framework.cron.domain import OnLook


class InProcessClock:
    """Looks every `look_every`, on the thread that called `run`, until `stop` — also when
    `stop` came first: a stopped clock does not start again.

    Context: `jitter` adds up to that much to each wait, so replicas started together stop
    claiming the same tick at the same instant. A cron runs within `look_every + jitter` of its
    time — seconds, for schedules measured in minutes.
    """

    def __init__(
        self, look_every: timedelta = timedelta(seconds=5), jitter: timedelta = timedelta(0)
    ) -> None:
        self.look_every = look_every
        self.jitter = jitter
        self._stopped = Event()

    def now(self) -> datetime:
        return datetime.now(UTC)

    def interval(self) -> timedelta:
        """How long until the next look."""
        return self.look_every + self.jitter * random.random()

    def run(self, look: OnLook) -> None:
        while not self._stopped.is_set():
            look(self.now())
            self._stopped.wait(self.interval().total_seconds())

    def stop(self) -> None:
        self._stopped.set()


class ManualClock:
    """Time moves when the test says so: `advance(minutes=2)` makes every gateway that runs on
    it look at that moment."""

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
