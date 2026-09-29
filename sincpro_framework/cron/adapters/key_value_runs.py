"""`KeyValueRuns`: the record of runs kept in a `KeyValueStore` — what crons on several replicas
share. On Redis, Valkey or Memcached every replica claims a tick and exactly one wins.

    KeyValueRuns(RedisKeyValue(redis.Redis.from_url(url)))

Context: `claim` is the store's atomic `add`, so the once-per-tick guarantee is the store's.
Beside each run the record keeps two pointers per cron — its latest run and its latest success —
which is all `running`, `last` and `last_success` read. `running` therefore answers for the
latest run: an older one still unfinished under a newer finished one is presumed dead, as it is
once it is older than `since`. Runs are kept for `retention`, then the store lets them go.
"""

from datetime import UTC, datetime, timedelta

from sincpro_framework.caching.store import KeyValueStore
from sincpro_framework.cron.domain import CronRuns, Run, RunOutcome


def _encoded(run: Run) -> bytes:
    return run.model_dump_json().encode()


def _decoded(held: bytes | None) -> Run | None:
    return None if held is None else Run.model_validate_json(held)


class KeyValueRuns(CronRuns):
    def __init__(
        self,
        store: KeyValueStore,
        retention: timedelta = timedelta(days=30),
        prefix: str = "cron",
    ) -> None:
        self.store = store
        self.retention = retention
        self.prefix = prefix

    def _run_key(self, name: str, scheduled_for: datetime, key: str) -> str:
        return f"{self.prefix}:run:{name}:{scheduled_for.isoformat()}:{key}"

    def _last_key(self, name: str) -> str:
        return f"{self.prefix}:last:{name}"

    def _success_key(self, name: str) -> str:
        return f"{self.prefix}:success:{name}"

    def claim(self, name: str, scheduled_for: datetime, key: str = "") -> bool:
        run = Run(
            name=name, scheduled_for=scheduled_for, key=key, started_at=datetime.now(UTC)
        )
        if not self.store.add(
            self._run_key(name, scheduled_for, key), _encoded(run), self.retention
        ):
            return False
        if key == "":
            latest = self.last(name)
            if latest is None or latest.scheduled_for <= scheduled_for:
                self.store.set(self._last_key(name), _encoded(run), self.retention)
        return True

    def finish(
        self, name: str, scheduled_for: datetime, key: str, outcome: RunOutcome
    ) -> None:
        run_key = self._run_key(name, scheduled_for, key)
        claimed = _decoded(self.store.get(run_key))
        if claimed is None:
            return
        finished = claimed.model_copy(
            update={"finished_at": datetime.now(UTC), "outcome": outcome}
        )
        self.store.set(run_key, _encoded(finished), self.retention)
        if key != "":
            return
        latest = self.last(name)
        if latest is not None and latest.scheduled_for == scheduled_for:
            self.store.set(self._last_key(name), _encoded(finished), self.retention)
        if outcome == RunOutcome.SUCCEEDED:
            self.store.set(self._success_key(name), _encoded(finished), self.retention)

    def running(self, name: str, since: datetime) -> bool:
        latest = self.last(name)
        return (
            latest is not None
            and latest.finished_at is None
            and latest.scheduled_for >= since
        )

    def last(self, name: str) -> Run | None:
        return _decoded(self.store.get(self._last_key(name)))

    def last_success(self, name: str) -> Run | None:
        return _decoded(self.store.get(self._success_key(name)))
