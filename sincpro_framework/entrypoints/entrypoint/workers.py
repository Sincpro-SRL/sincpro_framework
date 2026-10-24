"""Workers: one OS process that stays up and runs each loop until it is stopped — a pool of
loops, each on its own thread.

It starts the application layer the way a server does, without a request: each loop it runs —
crons on the clock, a poll of a relay — ends in an execution of the buses.

    from datetime import timedelta
    from sincpro_framework.entrypoints.entrypoint.workers import Poll, Process

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

    def run(self) -> None:
        """Block, doing the loop's work, until `stop` is called."""
        ...

    def stop(self) -> None:
        """Make `run` return — called from another thread, so it only signals."""
        ...


class Poll:
    """Call `tick` every `every`, on the thread that called `run`, until `stop`.

    The first call happens at once, then the wait. A `tick` that raises is logged and the
    next interval still runs. `stop` during a `tick` returns when that `tick` returns.
    """

    def __init__(self, every: timedelta, tick: Callable[[], None]) -> None:
        """Poll `tick` every `every` — a zero or negative interval is refused, it would spin.

            Poll(timedelta(seconds=5), relay.run_once)

        `tick` takes nothing and answers nothing — what it runs is the project's.
        """
        if every.total_seconds() <= 0:
            raise ValueError("a poll waits a positive interval")
        self.every = every
        self.tick = tick
        self._stopped = Event()

    def run(self) -> None:
        """Call `tick`, then wait the interval — or less, when `stop` wakes the wait — until
        stopped. A `tick` that raises is logged and the loop goes on.

            Poll(timedelta(seconds=5), relay.run_once).run()
            ->  run_once at 0 s, 5 s, 10 s, ... — returns once `stop` is called

        A `Poll` stopped before `run` returns at once, `tick` never called.
        """
        while not self._stopped.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("poll tick failed")
            self._stopped.wait(self.every.total_seconds())

    def stop(self) -> None:
        """Wake the wait and end the loop once the `tick` running, if any, returns."""
        self._stopped.set()


class Process:
    """Run each loop on its own thread until `stop`, or until SIGINT or SIGTERM.

    `run` is called from the main thread when it should handle those signals. A test passes
    `handle_signals=False` and calls `stop`. One loop ending, or raising out of `run`, is
    logged; the others keep going until `stop`.
    """

    def __init__(self, *loops: Loop) -> None:
        """Hold the loops to run — at least one; nothing starts until `run`.

            Process(CronGateway([billing_crons]), Poll(timedelta(seconds=5), tick))

        A loop is anything with `run` and `stop` (`Loop`).
        """
        if not loops:
            raise ValueError("a process runs at least one loop")
        self._loops = loops
        self._stopped = Event()
        self._threads: list[Thread] = []

    def _run_one(self, loop: Loop) -> None:
        """One loop's thread: what it raises is logged, never taken to the other loops."""
        try:
            loop.run()
        except Exception:
            logger.exception(f"{type(loop).__name__} stopped")

    def stop(self) -> None:
        """Ask every loop to return, and wait until each thread has.

            process.stop()     ->  None; every loop's `stop` called, every thread joined

        Called by `run` on its way out too, so a second call only joins finished threads.
        """
        self._stopped.set()
        for loop in self._loops:
            loop.stop()
        for thread in self._threads:
            thread.join()

    def _arm(self) -> dict[int, Any]:
        """Stop the process on SIGINT and SIGTERM — the handlers they had answered, so
        `_disarm` puts them back once the process ends.

        Context: a deployment stops a container with SIGTERM and a developer with Ctrl-C; both
        must let each loop finish its pass instead of killing it in the middle of a run.
        `signal.signal` only works on the main thread, which is why `run` is called there.
        """

        def on_signal(signum: int, frame: FrameType | None) -> None:
            """Log the signal and stop every loop."""
            logger.info(f"process stopping on signal {signum}")
            self.stop()

        armed: dict[int, Any] = {}
        for signum in (signal.SIGINT, signal.SIGTERM):
            armed[signum] = signal.signal(signum, on_signal)
        return armed

    def _disarm(self, previous: dict[int, Any]) -> None:
        """Give SIGINT and SIGTERM back the handlers they had before `run`."""
        for signum, handler in previous.items():
            signal.signal(signum, handler)

    def run(self, handle_signals: bool = True) -> None:
        """Run every loop on its own thread and block until the process is stopped.

        Context: the program's last line in a worker deployment — `Process(...).run()` — the
        way `uvicorn.run` is a server's. `handle_signals=False` for a caller off the main
        thread (a test), which then calls `stop` itself.

            Process(CronGateway([billing_crons]), Poll(timedelta(seconds=5), tick)).run()
            ->  threads "CronGateway" and "Poll" running; returns after SIGTERM, both done

        1. Arm SIGINT and SIGTERM, when asked.
        2. Start one non-daemon thread per loop, named after the loop's class.
        3. Wait until `stop` — called directly, or by a signal.
        Final: stop every loop, wait for every thread, and give the signals their handlers
        back — also when the wait is interrupted by an exception.
        """
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
