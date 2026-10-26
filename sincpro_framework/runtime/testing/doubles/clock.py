"""`ManualClock`: a cron gateway's clock that moves only when the test says so.

    clock = ManualClock(datetime(2026, 9, 26, 5, 59, tzinfo=UTC))      # 01:59 in La Paz
    gateway = CronGateway([cron_payments], clock=clock)
    gateway.run()                                                    # returns at once
    clock.advance(minutes=2)                                         # every gateway looks at 06:01

Context: it is a `Clock` of the crons (`now`, `run`, `stop`) like `InProcessClock`, written for
tests, so it lives beside the other doubles; a look is any callable taking the moment.
"""

from collections.abc import Callable
from datetime import datetime, timedelta


class ManualClock:
    """Time moves when the test says so: `advance(minutes=2)` makes every gateway that runs on
    it look at that moment."""

    def __init__(self, start: datetime) -> None:
        """`ManualClock(datetime(2026, 3, 1, 1, 59, tzinfo=UTC))` — refused without a
        timezone."""
        if start.tzinfo is None:
            raise ValueError("ManualClock needs an aware start")
        self._now = start
        self._looks: list[Callable[[datetime], None]] = []

    def now(self) -> datetime:
        """The moment the test last moved it to."""
        return self._now

    def run(self, look: Callable[[datetime], None]) -> None:
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
