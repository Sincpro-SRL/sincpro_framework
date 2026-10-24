# Leases: one holder at a time, across every replica

A lease holds a name — a sandbox, a run, an import of today's file — for one owner at a time, for as
long as its owner keeps renewing it. It is what a worker takes before work that must not run twice
at once, and what tells whether a worker that went quiet may still be at it.

Every block on this page runs, in order, in `tests/docs/test_persistence_guide.py`.

## Taking a name

```python
from datetime import timedelta

from sincpro_framework.store import InMemoryKeyValue, Leases, LeaseState
from sincpro_framework.store.domain import exceptions as store_exceptions

leases = Leases(InMemoryKeyValue())                    # RedisKeyValue, DynamoKeyValue on replicas

with leases.held("sandbox:S-1", owner="worker-a", ttl=timedelta(minutes=2)) as lease:
    assert leases.state("sandbox:S-1") is LeaseState.HELD
    lease.check()                                       # LeaseLost once somebody else may hold it
    try:
        with leases.held("sandbox:S-1", owner="worker-b"):
            pass
    except store_exceptions.LeaseTaken as taken:
        assert taken.holder == "worker-a"

assert leases.state("sandbox:S-1") is LeaseState.FREE
```

`held` takes the name — `LeaseTaken` when somebody holds it — renews it in the background every third
of its time while the block runs, and releases it at the end. `take`, `renew` and `release` are the
same steps one by one, for a holder that drives them itself.

## A holder that went quiet

```python
from datetime import UTC, datetime

clock = [datetime(2026, 10, 23, tzinfo=UTC)]
quiet = Leases(InMemoryKeyValue(now=lambda: clock[0]))

first = quiet.take("import:2026-10-23", owner="worker-a", ttl=timedelta(minutes=2))
clock[0] += timedelta(minutes=3)                        # it stopped renewing, without releasing

assert quiet.state("import:2026-10-23") is LeaseState.EXPIRED     # not FREE: it may still be at it
assert quiet.holder("import:2026-10-23") == "worker-a"

second = quiet.take("import:2026-10-23", owner="worker-b", ttl=timedelta(minutes=2))
assert first is not None and second is not None and second.fence > first.fence
try:
    first.renew()                                       # it woke up: the name is no longer its
except store_exceptions.LeaseLost:
    pass
assert quiet.holder("import:2026-10-23") == "worker-b"
```

| State | What it means | What a reclaimer does |
|---|---|---|
| `FREE` | never taken, or released | nothing |
| `HELD` | somebody holds it and renewed it in time | nothing: it is alive |
| `EXPIRED` | its holder stopped renewing without releasing | treats its work as abandoned — with care: the holder may still be writing |

**The fence.** Every take gets one more than the last holder's. A resource that remembers the last
fence it accepted refuses a write that carries an older one, so a holder that wakes up after a long
pause cannot overwrite what the new holder did — the fencing token of distributed locking.

## Why it is safe

- **Taking is one atomic `add`** with the lease's time: of many workers racing, one takes it.
- **Renewing and releasing are `replace`** — "only if it is still mine", one compare-and-write in
  the store: a holder whose time passed can neither extend nor free the lease another took.
- **Every store has it**: memory (a lock), Redis and Valkey (`WATCH` / `MULTI`), Memcached
  (`gets` / `cas`), DynamoDB (a conditional write). A store of a project's that cannot compare
  stays a store; a lease renewed on it says so.
- **A store that does not answer** is tried again at the next renewal; the lease's own time decides.

## A pipeline that holds one

```python
from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework
from sincpro_framework.context import InMemoryContexts, use_context
from sincpro_framework.pipelines import Pipeline, PipelineRun, PipelineState


class CommandImportDay(DataTransferObject):
    run_id: str


class CommandReadFile(DataTransferObject):
    run_id: str


imports = UseFramework("imports-leased", log_after_execution=False)
imports.store_context_in(InMemoryContexts())


@imports.feature(CommandReadFile)
class ReadFile(Feature):
    def execute(self, dto: CommandReadFile) -> None:
        return None


@imports.app_service(CommandImportDay)
class ImportDay(Pipeline, ApplicationService):
    steps = [CommandReadFile]
    lease = timedelta(minutes=2)                        # one run of a run_id at a time


done = imports(CommandImportDay(run_id="2026-10-23"), PipelineRun)
assert done.state is PipelineState.SUCCEEDED

with imports.context({}):
    assert use_context().pipelines().abandoned("imports-leased.CommandImportDay") == []
```

With `lease`, a run is taken before it is touched: a second execution of the same `run_id` — on
another replica — is `LeaseTaken` before it writes or runs a step. The lease is renewed while the
steps run; a worker that lost it stops before its next step, runs no `always` and writes the run no
more — it is another worker's now. `use_context().pipelines().abandoned(pipeline)` lists the runs
still `RUNNING` whose worker stopped renewing: what a cron marks failed. The lease is kept where the
context is kept, beside the drafts and the runs (`use_context().leases()`).

## What a lease is not

- **Not a row lock.** `for_update` holds rows for one transaction; a lease holds a name for minutes,
  across processes, and survives no transaction it is part of.
- **Not a business state.** A reservation a person reconciles, with its own events and screen, is an
  aggregate of the project's; a lease is the mechanics of staying alive under it.
