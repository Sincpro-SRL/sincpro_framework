"""`KeyValueRuns`: the record of runs stored in a `KeyValueStore` — what crons on several replicas
share. On Redis, Valkey or Memcached every replica claims a tick and exactly one wins.

    KeyValueRuns(RedisKeyValue(redis.Redis.from_url(url)))

Context: `claim` is the store's atomic `add`, so the once-per-tick guarantee is the store's.
Beside each run the record stores two pointers per cron — its latest run and its latest success —
which is all `running`, `last` and `last_success` read. `running` therefore answers for the
latest run: an older one still unfinished under a newer finished one is presumed dead, as it is
once it is older than `since`. Runs are retained for `retention`, then the store lets them go.
"""

from datetime import UTC, datetime, timedelta

from sincpro_framework.data_layer.store import KeyValueStore
from sincpro_framework.entrypoints.adapters.cron.domain.runs import CronRuns, Run, RunOutcome


def _encoded(run: Run) -> bytes:
    """A run as the bytes the store saves — its JSON."""
    return run.model_dump_json().encode()


def _decoded(held: bytes | None) -> Run | None:
    """What the store answered, back as a run — `None` for a key it does not have."""
    return None if held is None else Run.model_validate_json(held)


class KeyValueRuns(CronRuns):
    """The record of runs in a `KeyValueStore` the replicas share — one key per claimed run,
    two pointers per cron.

        cron:run:cron-payments.Reconcile:2026-03-02T06:00:00+00:00:         the tick's own run
        cron:run:cron-payments.Reconcile:2026-03-02T06:00:00+00:00:notify   a `Tick.once` step
        cron:last:cron-payments.Reconcile                                   its latest run
        cron:success:cron-payments.Reconcile                                its latest success
    """

    def __init__(
        self,
        store: KeyValueStore,
        retention: timedelta = timedelta(days=30),
        prefix: str = "cron",
    ) -> None:
        """`retention` is how long the store retains each key; `prefix` separates this record
        from anything else on the same store."""
        self.store = store
        self.retention = retention
        self.prefix = prefix

    def _run_key(self, name: str, scheduled_for: datetime, key: str) -> str:
        """The key of one claimed run — `<prefix>:run:<name>:<tick ISO>:<key>`."""
        return f"{self.prefix}:run:{name}:{scheduled_for.isoformat()}:{key}"

    def _last_key(self, name: str) -> str:
        """The key of the cron's latest-run pointer."""
        return f"{self.prefix}:last:{name}"

    def _success_key(self, name: str) -> str:
        """The key of the cron's latest-success pointer."""
        return f"{self.prefix}:success:{name}"

    def last(self, name: str) -> Run | None:
        """One read: the latest-run pointer."""
        return _decoded(self.store.get(self._last_key(name)))

    def claim(self, name: str, scheduled_for: datetime, key: str = "") -> bool:
        """Atomic with the store's `add` — set only when the key is absent, so exactly one
        replica wins.

            claim("cron-payments.Reconcile", datetime(2026, 3, 2, 6, tzinfo=UTC))
            ->  True on the first replica; cron:last:cron-payments.Reconcile now points at it
            claim("cron-payments.Reconcile", datetime(2026, 3, 2, 6, tzinfo=UTC))
            ->  False on every other one

        1. `add` the run's key with the new `Run`; already there — claimed elsewhere: `False`.
        2. The cron's own run (`key == ""`) moves the latest-run pointer, unless that already
           points at a later tick.
        3. Final: `True`.
        """
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
        """Record the end of a claimed run, and move the cron's pointers with it.

            finish("cron-payments.Reconcile", tick, "", RunOutcome.SUCCEEDED)
            ->  None; the run, cron:last:... and cron:success:... all hold it, finished
            finish("cron-payments.Reconcile", tick, "notify", RunOutcome.FAILED)
            ->  None; only that step's run is written, the pointers stay

        1. The claimed run is read back; one the store no longer has (retention passed) is
           left alone.
        2. It is written again with `finished_at` and `outcome`.
        3. A `Tick.once` step stops there — the pointers are the cron's own runs.
        4. The latest-run pointer is updated when it still points at this tick.
        5. Final: a success also becomes the latest-success pointer.
        """
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
        """Read from the latest-run pointer alone: unfinished and at or after `since`."""
        latest = self.last(name)
        return (
            latest is not None
            and latest.finished_at is None
            and latest.scheduled_for >= since
        )

    def last_success(self, name: str) -> Run | None:
        """One read: the latest-success pointer."""
        return _decoded(self.store.get(self._success_key(name)))
