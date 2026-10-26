"""The clock a gateway runs on: `InProcessClock` looks on a loop of its own. The one a test moves by
hand, `ManualClock`, is in `sincpro_framework.runtime.testing`."""

import random
from datetime import UTC, datetime, timedelta
from threading import Event

from sincpro_framework.entrypoints.adapters.cron.domain.clock import OnLook


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
