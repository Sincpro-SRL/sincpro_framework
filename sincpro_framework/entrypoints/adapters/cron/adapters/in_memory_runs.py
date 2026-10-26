"""`InMemoryRuns`: the record of runs held in the process — enough for crons that run on one
replica. Crons that run on several replicas need a record those replicas share: the project
implements `CronRuns` on its own storage."""

from datetime import UTC, datetime
from threading import Lock

from sincpro_framework.entrypoints.adapters.cron.domain.runs import CronRuns, Run, RunOutcome


class InMemoryRuns(CronRuns):
    """The record of runs in a dict, behind a lock — the default of a `CronGateway` given no
    `runs`.

        CronGateway([cron_payments])                       # InMemoryRuns: one replica
        CronGateway([cron_payments], runs=KeyValueRuns(store))   # several replicas

    Context: every run is held for the life of the process, keyed by
    `(name, scheduled_for, key)`; a restart forgets them, so a cron starts again from now —
    fine on one replica, wrong on several, where each would run every tick.
    """

    def __init__(self) -> None:
        """An empty record, and the lock every read and write takes."""
        self._runs: dict[tuple[str, datetime, str], Run] = {}
        self._lock = Lock()

    def claim(self, name: str, scheduled_for: datetime, key: str = "") -> bool:
        """Atomic under the process's lock: the first claim of the key stores its `Run`.

            claim("billing.Reconcile", datetime(2026, 3, 2, 6, tzinfo=UTC))  ->  True
            claim("billing.Reconcile", datetime(2026, 3, 2, 6, tzinfo=UTC))  ->  False

        The key is the cron, the tick and the step (`key`), so each `Tick.once` step of the
        same tick is claimed on its own.
        """
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
        """Replace the claimed `Run` with a copy that has `finished_at` and `outcome` — a run
        never claimed here raises `KeyError`."""
        with self._lock:
            run = self._runs[(name, scheduled_for, key)]
            self._runs[(name, scheduled_for, key)] = run.model_copy(
                update={"finished_at": datetime.now(UTC), "outcome": outcome}
            )

    def _own(self, name: str) -> list[Run]:
        """The cron's own runs — `key == ""`, not the steps `Tick.once` claimed — oldest tick
        first."""
        with self._lock:  # another cron's thread claims while this one reads
            runs = [run for run in self._runs.values() if run.name == name and run.key == ""]
        return sorted(runs, key=lambda run: run.scheduled_for)

    def running(self, name: str, since: datetime) -> bool:
        """Any unfinished run of a tick at or after `since` — every run is retained, so unlike
        `KeyValueRuns` an older unfinished one under a newer finished one still counts."""
        return any(
            run.finished_at is None and run.scheduled_for >= since for run in self._own(name)
        )

    def last(self, name: str) -> Run | None:
        """The run of the latest tick, by `scheduled_for`."""
        runs = self._own(name)
        return runs[-1] if runs else None

    def last_success(self, name: str) -> Run | None:
        """The latest tick whose run ended `SUCCEEDED`."""
        succeeded = [run for run in self._own(name) if run.outcome == RunOutcome.SUCCEEDED]
        return succeeded[-1] if succeeded else None
