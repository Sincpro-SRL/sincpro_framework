"""One OS process that stays up and runs each loop until it is stopped.

    from datetime import timedelta
    from sincpro_framework.process import Poll, Process

    def tick() -> None:
        relay.run_once()

    Process(
        CronGateway([billing_crons]),
        Poll(timedelta(seconds=5), tick),
    ).run()

A loop is anything with `run` and `stop`. `CronGateway` is one. `Poll` is the other this
module ships: every interval it calls the function the project gave it. That function is a
Command, a relay pass, or whatever the project writes. This module does not know the table,
the row, or the broker.

`faststream run` stays the broker's program. A server stays its gateway. This process is how
a deployment that is not answering a request keeps those loops, one or several.
"""

import signal
from collections.abc import Callable
from datetime import timedelta
from threading import Event, Thread
from types import FrameType
from typing import Any, Protocol

from sincpro_framework.sincpro_logger import logger


class Loop(Protocol):
    """A loop this process can run: `run` blocks, `stop` makes it return."""

    def run(self) -> None: ...

    def stop(self) -> None: ...


class Poll:
    """Call `tick` every `every`, on the thread that called `run`, until `stop`.

    The first call happens at once, then the wait. A `tick` that raises is logged and the
    next interval still runs. `stop` during a `tick` returns when that `tick` returns.
    """

    def __init__(self, every: timedelta, tick: Callable[[], None]) -> None:
        if every.total_seconds() <= 0:
            raise ValueError("a poll waits a positive interval")
        self.every = every
        self.tick = tick
        self._stopped = Event()

    def run(self) -> None:
        while not self._stopped.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("poll tick failed")
            self._stopped.wait(self.every.total_seconds())

    def stop(self) -> None:
        self._stopped.set()


class Process:
    """Run each loop on its own thread until `stop`, or until SIGINT or SIGTERM.

    `run` is called from the main thread when it should handle those signals. A test passes
    `handle_signals=False` and calls `stop`. One loop ending, or raising out of `run`, is
    logged; the others keep going until `stop`.
    """

    def __init__(self, *loops: Loop) -> None:
        if not loops:
            raise ValueError("a process runs at least one loop")
        self._loops = loops
        self._stopped = Event()
        self._threads: list[Thread] = []

    def run(self, *, handle_signals: bool = True) -> None:
        previous = self._arm() if handle_signals else {}
        try:
            for loop in self._loops:
                thread = Thread(
                    target=self._run_one, args=(loop,), name=type(loop).__name__, daemon=False
                )
                self._threads.append(thread)
                thread.start()
            self._stopped.wait()
        finally:
            self.stop()
            self._disarm(previous)

    def stop(self) -> None:
        """Ask every loop to return, and wait until each thread has."""
        self._stopped.set()
        for loop in self._loops:
            loop.stop()
        for thread in self._threads:
            thread.join()

    def _run_one(self, loop: Loop) -> None:
        try:
            loop.run()
        except Exception:
            logger.exception(f"{type(loop).__name__} stopped")

    def _arm(self) -> dict[int, Any]:
        def on_signal(signum: int, frame: FrameType | None) -> None:
            logger.info(f"process stopping on signal {signum}")
            self.stop()

        armed: dict[int, Any] = {}
        for signum in (signal.SIGINT, signal.SIGTERM):
            armed[signum] = signal.signal(signum, on_signal)
        return armed

    def _disarm(self, previous: dict[int, Any]) -> None:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
