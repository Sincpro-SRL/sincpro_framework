# Crons

Deep doc in the framework repo: `docs/cron/README.md`, PRD_08 (every block there runs as a test).
This page stands alone.

A cron is a **caller** of use cases, not a use case: a class in a registry of its own per bounded
context, the buses it orchestrates injected by their own names.

## Registry and base

```python
from datetime import timedelta

from sincpro_framework import UseFramework
from sincpro_framework.cron import Cron as _Cron, Crons, Missed, Overlap, Tick


class CronDependencyContextType:                 # infrastructure/dependencies.py
    cybersource: UseFramework
    siat_soap_sdk: UseFramework


class Cron(_Cron, CronDependencyContextType):    # infrastructure/framework.py
    """Base cron of this bounded context — typed dependencies included."""


cron_payments = Crons[CronDependencyContextType]("cron-payments")
cron_payments.add_dependency("cybersource", cybersource)
cron_payments.add_dependency("siat_soap_sdk", siat_soap_sdk)


@cron_payments.cron(
    "0 2 * * *",
    timezone="America/La_Paz",
    overlap=Overlap.SKIP,
    missed=Missed.RUN_LATEST,
    missed_window=timedelta(days=1),
)
class ReconcileTransactions(Cron):
    def run(self, tick: Tick) -> None:
        pending = self.cybersource(QueryPending(day=tick.scheduled_for), ResponsePending)
        for transaction_id in pending.transactions:
            if tick.once(transaction_id):
                self.siat_soap_sdk(CommandIssueInvoice(transaction_id=transaction_id))
```

- `@crons.cron("…", timezone=…)` — five fields with `*`, lists, ranges, steps and names
  (`MON-FRI`, `JAN,JUL`). A timezone is required (`ValueError` without one); DST is evaluated in
  it: a tick inside the spring-forward gap runs shifted past it, the repeated fall-back hour once.
- `@crons.cron(every=timedelta(minutes=15))` — an interval counted from the Unix epoch, so every
  replica computes the same ticks whenever it started. One of an expression or `every=`, never both.
- The cron's name — in runs, logs, status — is `<registry>.<Class>` unless `name=` is given.
- Every dependency of the registry is set on every cron; `cron_payments.deps.cybersource` reaches
  the same instance from outside. A cron (or its base) annotating a dependency the registry lacks
  raises `DependencyNotRegistered` when the registry is built — `CronGateway(...)` builds it.
- One instance serves every tick, from several threads: state lives in locals.
- `tick.once(key: str)` is `True` the first time `key` is asked for this tick, on any replica sharing
  `runs`. At most once: a step that fails after `once` is not retried by another run of that tick.
- Every bus the cron calls sees the tick in its context (`cron`, `scheduled_for`); the run has a
  span of kind `cron`. A failure — even inside a bus — is logged once by the registry with
  `failed_in` naming the bus, recorded `FAILED`, and never raised to the clock.

## Running them

**Beside a service, in a process of their own (default):**

```python
from sincpro_framework.cron import CronGateway, CronProcess


def build_crons() -> CronGateway:      # module level: the spawned child imports and calls it
    return CronGateway([cron_payments])


crons = CronProcess(build_crons).start()   # at startup
crons.stop()                               # at shutdown: finishes runs in progress, terminates after 30 s
```

The child is spawned (or `context="forkserver"`), never forked — a forked child would inherit the
parent's database connections — and builds its own gateway, buses and pools: a bus cannot cross a
process. The service's requests and its crons share no thread, pool or crash.

**As a deployment of its own:**

```python
CronGateway([cron_payments, cron_billing], runs=shared_runs).run()   # blocks on the clock
```

Inside, the gateway looks every 5 s (`look_every=`), plus up to `jitter=` so replicas started
together do not claim at the same instant; claims each due tick in `runs` before running it; and
gives each run a thread of its own (`workers=` caps how many at once). The ticks of one cron run in
order. On start it logs one line per cron with its next tick, so a process waiting for its first
tick is not mistaken for a stuck one. `stop()` stops looking and waits for the runs in progress.

## One run per tick

Before a cron runs, its tick is claimed in `runs`: the first claim of `(name, scheduled_for)` wins,
every other caller does nothing. That claim is the whole mechanism — no lock, no leader.

- **One replica runs the crons**: `InMemoryRuns` (the default) is enough. A restart starts from
  now, so a tick that fell while it was down is not run.
- **Several replicas run the crons**: each would run every tick on `InMemoryRuns`. Give them a
  shared `CronRuns`:

```python
import redis

from sincpro_framework.caching.adapters.redis import RedisKeyValue   # extra [redis]
from sincpro_framework.cron import KeyValueRuns

shared_runs = KeyValueRuns(RedisKeyValue(redis.Redis.from_url(url)))   # retention=30 days, prefix="cron"
```

  `claim` is the store's atomic `add`, so the once-per-tick guarantee is the store's. A gateway on
  shared runs starts from where each cron last ran, so `missed` sees ticks that passed while down.
  Any other storage implements `CronRuns` (`claim`, `finish`, `running`, `last`, `last_success`).

| Policy | Values | Default |
|---|---|---|
| `overlap` | `SKIP` (a tick while the previous run goes is recorded `SKIPPED`), `ALLOW` | `SKIP` |
| `missed` | `SKIP`, `RUN_LATEST`, `RUN_ALL` (in order) | `RUN_LATEST` |
| `missed_window` | a `timedelta`: nothing older runs | 1 day |
| `stale_after` | a run unfinished this long past its tick is presumed dead and stops holding `overlap=SKIP` | 1 hour |

A tick more than a minute behind the clock counts as missed.

## Seeing, replacing, running now

```python
gateway.plan(until=...)                                    # [(name, tick)], the next ticks, nothing runs
gateway.status()["cron-payments.ReconcileTransactions"]    # CronStatus: last_run, last_success, next_tick
gateway.run_now(ReconcileWithTheBank)                      # one run now, this thread; claimed and recorded
```

"No success since X" per cron is the alert that catches a clock that silently stopped.

`@crons.cron(..., replaces=Reconcile)` runs the new class under the replaced cron's name (its
record of runs and alerts go on) with the schedule given there; `crons.without(Remind)` switches
one off. Replacing or switching off a cron the registry does not have only logs a warning; both
raise `BusAlreadyBuilt` once the registry is built.

## Driving an event relay

```python
relay = crons.relay_deliverable_events(
  repository=repository,
  source=BillingEvent,
  to=publisher,
  every=timedelta(seconds=2),
)
```

Or register an existing `EventRelay` with `crons.run_relay(relay, every=...)`. Both schedule
`run_once`; the relay itself has no timer or worker. Register before building the gateway.
This does not create an event table or make an event deliverable: map the context base and
use `DeliverableEventMixin` first (`sincpro-framework-domain-events`).

Replica exclusion for rows depends on database lock capabilities, independently of the cron's
shared `CronRuns`. Delivery remains at least once. Strict ordering across backoff intervals
and replicas is not currently guaranteed by `EventRelay`.

## Testing

```python
from datetime import UTC, datetime

from sincpro_framework.cron import CronGateway, InMemoryRuns, RunOutcome
from sincpro_framework.testing import ManualClock

clock = ManualClock(datetime(2026, 9, 26, 5, 59, tzinfo=UTC))      # 01:59 in La Paz
gateway = CronGateway([cron_payments], clock=clock, runs=InMemoryRuns())
gateway.run()                                                       # returns: the clock is manual
clock.advance(minutes=2)
gateway.wait()
assert gateway.status()["cron-payments.ReconcileTransactions"].last_run.outcome == RunOutcome.SUCCEEDED
```
