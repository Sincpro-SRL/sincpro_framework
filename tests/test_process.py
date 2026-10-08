"""A process stays up and runs whatever loops it was given, until stop."""

from datetime import timedelta
from threading import Event, Thread

import pytest

from sincpro_framework.process import Poll, Process


class _Hold:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()
        self.stopped = False

    def run(self) -> None:
        self.started.set()
        self.release.wait()

    def stop(self) -> None:
        self.stopped = True
        self.release.set()


def test_a_process_needs_a_loop():
    with pytest.raises(ValueError, match="at least one loop"):
        Process()


def test_a_process_runs_each_loop_until_stop():
    first, second = _Hold(), _Hold()
    process = Process(first, second)
    thread = Thread(target=lambda: process.run(handle_signals=False))
    thread.start()

    assert first.started.wait(2) and second.started.wait(2)
    process.stop()
    thread.join(2)

    assert not thread.is_alive()
    assert first.stopped and second.stopped


def test_a_poll_calls_tick_at_once_and_stop_ends_the_wait():
    calls = Event()
    poll = Poll(timedelta(seconds=30), calls.set)
    thread = Thread(target=poll.run)
    thread.start()

    assert calls.wait(2)
    poll.stop()
    thread.join(2)

    assert not thread.is_alive()


def test_a_poll_interval_is_positive():
    with pytest.raises(ValueError, match="positive"):
        Poll(timedelta(0), lambda: None)


def test_a_failing_tick_does_not_end_the_poll():
    calls: list[int] = []
    again = Event()

    def tick() -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        again.set()

    poll = Poll(timedelta(milliseconds=20), tick)
    thread = Thread(target=poll.run)
    thread.start()

    assert again.wait(2)
    poll.stop()
    thread.join(2)

    assert len(calls) >= 2


def test_a_loop_that_raises_does_not_stop_the_other():
    class _Boom:
        def run(self) -> None:
            raise RuntimeError("down")

        def stop(self) -> None:
            return None

    held = _Hold()
    process = Process(_Boom(), held)
    thread = Thread(target=lambda: process.run(handle_signals=False))
    thread.start()

    assert held.started.wait(2)
    process.stop()
    thread.join(2)

    assert not thread.is_alive() and held.stopped
