"""`InMemoryRuns`: the record of runs kept in the process — enough for crons that run on one
replica. Crons that run on several replicas need a record those replicas share: the project
implements `CronRuns` on its own storage."""

from datetime import UTC, datetime
from threading import Lock

from sincpro_framework.cron.domain import CronRuns, Run, RunOutcome


class InMemoryRuns(CronRuns):
    def __init__(self) -> None:
        self._runs: dict[tuple[str, datetime, str], Run] = {}
        self._lock = Lock()

    def claim(self, name: str, scheduled_for: datetime, key: str = "") -> bool:
        with self._lock:
            if (name, scheduled_for, key) in self._runs:
                return False
            self._runs[(name, scheduled_for, key)] = Run(
                name=name, scheduled_for=scheduled_for, key=key, started_at=datetime.now(UTC)
            )
            return True

    def finish(
        self, name: str, scheduled_for: datetime, key: str, outcome: RunOutcome
    ) -> None:
        with self._lock:
            run = self._runs[(name, scheduled_for, key)]
            self._runs[(name, scheduled_for, key)] = run.model_copy(
                update={"finished_at": datetime.now(UTC), "outcome": outcome}
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
