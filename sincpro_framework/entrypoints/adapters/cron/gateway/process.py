"""`CronProcess`: the crons in a process of their own — the default way to run them beside a
service.

    def build_crons() -> CronGateway:           # a module-level function: it runs in the child
        return CronGateway([cron_payments])

    crons = CronProcess(build_crons).start()    # at service startup
    ...
    crons.stop()                                # at shutdown: finishes the runs in progress

Context: a service's requests and its crons do not share a thread, a pool or a crash. The child
is spawned, never forked, so what `build` creates — engines, pools, buses — is its own; a bus
cannot cross a process anyway, which is why the child builds the gateway itself. The same shape
as `BackgroundQueue`. A deployment that is only crons runs `CronGateway(...).run()` directly.
"""

import multiprocessing
from collections.abc import Callable
from multiprocessing.context import ForkServerProcess, SpawnProcess
from multiprocessing.synchronize import Event
from threading import Thread
from typing import Literal

from sincpro_framework.entrypoints.adapters.cron.gateway.gateway import CronGateway


def _serve(build: Callable[[], CronGateway], stop: Event) -> None:
    """The child: build the gateway, stop it when the parent says so, run until then."""
    gateway = build()

    def stop_when_told() -> None:
        """Wait for the parent's event, then stop the gateway — which ends `run`."""
        stop.wait()
        gateway.stop()

    stopper = Thread(target=stop_when_told, daemon=True)
    stopper.start()
    gateway.run()
    stopper.join()


class CronProcess:
    """Context: never forked — a forked child inherits the parent's database connections — so
    `context` is `spawn` or `forkserver`."""

    def __init__(
        self,
        build: Callable[[], CronGateway],
        context: Literal["spawn", "forkserver"] = "spawn",
    ) -> None:
        """`build` is a module-level function — the child imports it by name — that builds the
        `CronGateway`; nothing starts until `start`."""
        self.build = build
        self._context = (
            multiprocessing.get_context("forkserver")
            if context == "forkserver"
            else multiprocessing.get_context("spawn")
        )
        self._stop = self._context.Event()
        self.process: SpawnProcess | ForkServerProcess | None = None

    def start(self) -> "CronProcess":
        """Spawn the child — a daemon named `crons` that builds the gateway and runs it — and
        hand this back, so `crons = CronProcess(build_crons).start()` reads in one line.

            CronProcess(build_crons).start()   ->  the same CronProcess, is_alive() True

        The stop event is cleared first, so a process stopped before can be started again.
        """
        self._stop.clear()
        process = self._context.Process(
            target=_serve, args=(self.build, self._stop), name="crons", daemon=True
        )
        process.start()
        self.process = process
        return self

    def is_alive(self) -> bool:
        """Whether the child was started and has not exited — what a liveness probe asks."""
        return self.process is not None and self.process.is_alive()

    def stop(self, timeout: float = 30.0) -> None:
        """Ask the child to stop looking and finish its runs; terminate it after `timeout`.

            crons.stop()            ->  None; runs in progress finished, the child exited
            crons.stop(timeout=5)   ->  None; a child still running after 5 s is terminated
            CronProcess(build_crons).stop()   ->  None, nothing was started

        Either way `process` is `None` after it, so `is_alive` answers `False`.
        """
        if self.process is None:
            return
        self._stop.set()
        self.process.join(timeout)
        if self.process.is_alive():
            self.process.terminate()
            self.process.join(1)
        self.process = None
