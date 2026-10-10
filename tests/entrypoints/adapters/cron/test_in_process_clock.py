"""`InProcessClock` on a real thread: it ticks until stopped, and stopping ends `run`."""

from datetime import datetime, timedelta
from threading import Event, Thread

from sincpro_framework.entrypoints.adapters.cron import (
    Cron,
    CronGateway,
    Crons,
    InMemoryRuns,
    Tick,
)
from sincpro_framework.entrypoints.adapters.cron.adapters import InProcessClock


def test_the_clock_ticks_on_its_thread_until_it_is_stopped():
    beats: list[datetime] = []
    enough = Event()
    crons = Crons("cron-heartbeat")

    @crons.cron(every=timedelta(milliseconds=20))
    class Beat(Cron):
        def run(self, tick: Tick) -> None:
            beats.append(tick.scheduled_for)
            if len(beats) >= 3:
                enough.set()

    gateway = CronGateway(
        [crons],
        look_every=timedelta(milliseconds=5),
        runs=InMemoryRuns(),
    )
    worker = Thread(target=gateway.run)
    worker.start()

    assert enough.wait(timeout=5)
    gateway.stop()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert beats == sorted(beats) and len(set(beats)) == len(beats)


def test_the_clock_looks_every_five_seconds_by_default():
    assert InProcessClock().interval() == timedelta(seconds=5)


def test_jitter_spreads_each_look_after_look_every():
    clock = InProcessClock(look_every=timedelta(seconds=5), jitter=timedelta(seconds=2))

    intervals = {clock.interval() for _ in range(50)}

    assert all(timedelta(seconds=5) <= one <= timedelta(seconds=7) for one in intervals)
    assert len(intervals) > 1


def test_a_clock_stopped_before_it_runs_does_not_start():
    clock = InProcessClock(look_every=timedelta(milliseconds=5))
    clock.stop()

    looks: list[datetime] = []
    worker = Thread(target=clock.run, args=(looks.append,))
    worker.start()
    worker.join(timeout=1)

    assert not worker.is_alive()
    assert looks == []
