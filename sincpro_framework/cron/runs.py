"""The once-per-tick guard: every replica may tick, one claim wins.

Context: `claim(name, scheduled_for, key)` is the whole mechanism — the first caller gets `True`
and runs; every other caller, on this replica or another, gets `False` and does nothing. The same
record answers what a cron needs to decide the next tick: whether a run is still going, the
last one, the last success. `InMemoryRuns` is one process; `DatabaseRuns`
(`sincpro_framework.cron.database`) is every replica sharing a table.
"""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from threading import Lock
from typing import Protocol


class RunOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class Run:
    name: str
    scheduled_for: datetime
    key: str
    started_at: datetime
    finished_at: datetime | None = None
    outcome: RunOutcome | None = None


class CronRuns(Protocol):
    def claim(self, name: str, scheduled_for: datetime, key: str = "") -> bool: ...

    def finish(
        self, name: str, scheduled_for: datetime, key: str, outcome: RunOutcome
    ) -> None: ...

    def running(self, name: str, since: datetime) -> bool:
        """Whether a run of this cron for a tick at or after `since` was claimed and has not
        finished. Context: an older unfinished run is presumed dead — its replica crashed or was
        terminated — and no longer holds the cron."""
        ...

    def last(self, name: str) -> Run | None:
        """The run of the latest tick — the cron's own, not a key claimed inside it."""
        ...

    def last_success(self, name: str) -> Run | None: ...


class InMemoryRuns:
    def __init__(self) -> None:
        self._runs: dict[tuple[str, datetime, str], Run] = {}
        self._lock = Lock()

    def claim(self, name: str, scheduled_for: datetime, key: str = "") -> bool:
        with self._lock:
            if (name, scheduled_for, key) in self._runs:
                return False
            self._runs[(name, scheduled_for, key)] = Run(
                name, scheduled_for, key, started_at=datetime.now(UTC)
            )
            return True

    def finish(
        self, name: str, scheduled_for: datetime, key: str, outcome: RunOutcome
    ) -> None:
        with self._lock:
            run = self._runs[(name, scheduled_for, key)]
            self._runs[(name, scheduled_for, key)] = replace(
                run, finished_at=datetime.now(UTC), outcome=outcome
            )

    def _own(self, name: str) -> list[Run]:
        return sorted(
            (run for run in self._runs.values() if run.name == name and run.key == ""),
            key=lambda run: run.scheduled_for,
        )

    def running(self, name: str, since: datetime) -> bool:
        return any(
            run.finished_at is None and run.scheduled_for >= since for run in self._own(name)
        )

    def last(self, name: str) -> Run | None:
        runs = self._own(name)
        return runs[-1] if runs else None

    def last_success(self, name: str) -> Run | None:
        succeeded = [run for run in self._own(name) if run.outcome == RunOutcome.SUCCEEDED]
        return succeeded[-1] if succeeded else None
