"""`CronProcess`: the crons in a spawned process of their own, started and stopped from the
service's.

The child builds its own gateway from a module-level function — a bus cannot cross a process —
and writes to a file the parent watches.
"""

import os
import time
from datetime import timedelta
from pathlib import Path

from sincpro_framework.entrypoints.adapters.cron import (
    Cron,
    CronGateway,
    CronProcess,
    Crons,
    Tick,
)

OUTPUT = "SINCPRO_CRON_TEST_OUTPUT"


def build_crons() -> CronGateway:
    """Runs in the child: everything the gateway needs is built here."""
    crons = Crons("cron-child")
    output = Path(os.environ[OUTPUT])

    @crons.cron(every=timedelta(milliseconds=20))
    class Mark(Cron):
        def run(self, tick: Tick) -> None:
            with output.open("a") as marks:
                marks.write(f"{os.getpid()}\n")

    return CronGateway([crons], look_every=timedelta(milliseconds=5))


def test_the_crons_run_in_their_own_process_until_stopped(tmp_path, monkeypatch):
    output = tmp_path / "marks.txt"
    output.touch()
    monkeypatch.setenv(OUTPUT, str(output))

    crons = CronProcess(build_crons).start()
    deadline = time.monotonic() + 20
    while len(output.read_text().splitlines()) < 2 and time.monotonic() < deadline:
        time.sleep(0.05)
    crons.stop()

    pids = set(output.read_text().splitlines())
    assert len(output.read_text().splitlines()) >= 2
    assert pids and str(os.getpid()) not in pids
    assert not crons.is_alive()
