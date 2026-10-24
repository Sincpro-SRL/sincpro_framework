"""The clocks: `InProcessClock` looks on a loop of its own, `ManualClock` when a test says so."""

import random
from datetime import UTC, datetime, timedelta
from threading import Event

from sincpro_framework.entrypoints.adapters.cron.domain import OnLook


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
        """`InProcessClock(timedelta(seconds=5), jitter=timedelta(seconds=2))` — a look
        every 5 to 7 seconds."""
        self.look_every = look_every
        self.jitter = jitter
        self._stopped = Event()

    def now(self) -> datetime:
        """The system time, in UTC."""
        return datetime.now(UTC)

    def interval(self) -> timedelta:
        """How long until the next look."""
        return self.look_every + self.jitter * random.random()

    def run(self, look: OnLook) -> None:
        """Look now, then after every interval — blocking the calling thread until `stop`.

        Context: the wait is on the stop event, so `stop` ends it at once instead of after the
        current interval.
        """
        while not self._stopped.is_set():
            look(self.now())
            self._stopped.wait(self.interval().total_seconds())

    def stop(self) -> None:
        """End `run`'s loop — for good: this clock never looks again."""
        self._stopped.set()


class ManualClock:
    """Time moves when the test says so: `advance(minutes=2)` makes every gateway that runs on
    it look at that moment."""

    def __init__(self, start: datetime) -> None:
        """`ManualClock(datetime(2026, 3, 1, 1, 59, tzinfo=UTC))` — refused without a
        timezone."""
        if start.tzinfo is None:
            raise ValueError("ManualClock needs an aware start")
        self._now = start
        self._looks: list[OnLook] = []

    def now(self) -> datetime:
        """The moment the test last moved it to."""
        return self._now

    def run(self, look: OnLook) -> None:
        """Remember `look` and return at once — the test drives it with `advance`, so
        `gateway.run()` does not block a test."""
        self._looks.append(look)

    def stop(self) -> None:
        """Forget every `look`: an `advance` after it reaches no gateway."""
        self._looks.clear()

    def advance(
        self, days: int = 0, hours: int = 0, minutes: int = 0, seconds: int = 0
    ) -> None:
        """Move time forward, then make every gateway running on this clock look at the new
        now.

            clock.advance(minutes=2)    # 01:59 -> 02:01: a "0 2 * * *" cron starts its run

        Context: the runs start on their own threads, so a test calls `gateway.wait()` before
        it asserts what they did.
        """
        self._now += timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
        for look in list(self._looks):
            look(self._now)
