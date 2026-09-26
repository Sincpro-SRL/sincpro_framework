# Crons: callers of use cases on a clock

A cron is not a use case: it is a **caller** of use cases, like an RPC method or an MCP tool. It
lives beside the buses, in a registry of its own per bounded context, and orchestrates the buses
it is given.

Every block on this page runs, in order, in `tests/docs/test_persistence_guide.py`.

## The buses it orchestrates

```python
from datetime import UTC, datetime, timedelta

from sincpro_framework import DataTransferObject, Feature, UseFramework


class QueryPending(DataTransferObject):
    day: datetime


class ResponsePending(DataTransferObject):
    transactions: list[str]


class CommandIssueInvoice(DataTransferObject):
    transaction_id: str


cybersource = UseFramework("cybersource", log_after_execution=False)
siat_soap_sdk = UseFramework("siat-soap-sdk", log_after_execution=False)
issued: list[tuple[str, str]] = []


@cybersource.feature(QueryPending)
class Pending(Feature):
    def execute(self, dto: QueryPending) -> ResponsePending:
        return ResponsePending(transactions=["tx-1", "tx-2"])


@siat_soap_sdk.feature(CommandIssueInvoice)
class IssueInvoice(Feature):
    def execute(self, dto: CommandIssueInvoice) -> None:
        issued.append((dto.transaction_id, self.context["cron"]))
```

## A registry per bounded context, a class per cron

```python
from sincpro_framework.cron import Cron, Crons, Missed, Overlap, Tick

cron_payments = Crons("cron-payments")                      # its logger and trace identity
cron_payments.add_dependency("cybersource", cybersource)    # the real buses, by their name
cron_payments.add_dependency("siat_soap_sdk", siat_soap_sdk)


@cron_payments.cron(
    "0 2 * * *",
    timezone="America/La_Paz",
    overlap=Overlap.SKIP,            # the previous run still going: skip this tick
    missed=Missed.RUN_LATEST,        # ticks missed while down: run only the latest
    missed_window=timedelta(days=1),
)
class ReconcileTransactions(Cron):
    cybersource: UseFramework
    siat_soap_sdk: UseFramework

    def run(self, tick: Tick) -> None:
        pending = self.cybersource(QueryPending(day=tick.scheduled_for), ResponsePending)
        for transaction in pending.transactions:
            if tick.once(transaction):                      # this one, once per tick
                self.siat_soap_sdk(CommandIssueInvoice(transaction_id=transaction))
```

- `@crons.cron("…", timezone=…)` — the five standard cron fields with `*`, lists, ranges, steps
  and names (`MON-FRI`, `JAN,JUL`). A timezone is required; DST is evaluated in it: a tick inside
  the spring-forward gap runs at the next valid instant, one inside the fall-back repeat runs once.
- `@crons.cron(every=timedelta(minutes=15))` — at :00, :15, :30 and :45 UTC: an interval counts
  from the Unix epoch, so every replica computes the same ticks whenever it started.
- The dependencies are annotated attributes, injected like a Feature's. A cron that declares one
  the registry does not have fails when the registry is built, not at 02:00.
- One instance serves every tick: state lives in locals.
- `tick.once(key)` — `True` the first time `key` is asked for this tick, on any replica: a step
  that must not be repeated when the tick is.

## Run them

**Beside a service, in a process of their own** — the default:

```python
from sincpro_framework.cron import CronGateway, CronProcess


def build_crons() -> CronGateway:        # a module-level function: it runs in the child
    return CronGateway([cron_payments])  # + runs=DatabaseRuns(...) in production


# crons = CronProcess(build_crons).start()      at service startup
# crons.stop()                                   at shutdown: finishes the runs in progress
```

The child is spawned, never forked, and builds its own gateway: a bus cannot cross a process,
and the service's requests and its crons share no thread, pool or crash. The same shape as
`BackgroundQueue`.

**As a deployment of its own**: `CronGateway([cron_payments, cron_billing], runs=…).run()` blocks
the process on the clock; `stop()` ends it and waits for the runs in progress.

Inside, the gateway is an in-memory orchestrator for every cron of every registry it is given:

- **when to look** — `InProcessClock` looks every 5 seconds (`resolution=`); `jitter=` adds up to
  that much to each wait, so replicas started together do not claim at the same instant;
- **what is due** — each look asks every cron's trigger, and a due tick is claimed in `runs`
  before it runs, so it runs once across replicas;
- **who runs it** — each run has a thread of its own, so a long cron never delays another's
  tick; `workers=` caps how many run at once. The ticks of one cron run in
  order.

```
service process ── CronProcess ──▶ child process
                                    └─ CronGateway (in memory)
                                        ├─ InProcessClock: looks every 5 s (+ jitter)
                                        └─ a thread per run ─▶ Cron.run(tick) ─▶ buses
```

Here the clock is a `ManualClock`, so time moves when the example says so:

```python
from sincpro_framework.cron import ManualClock, InMemoryRuns, RunOutcome

clock = ManualClock(datetime(2026, 9, 26, 5, 59, tzinfo=UTC))       # 01:59 in La Paz
gateway = CronGateway([cron_payments], clock=clock, runs=InMemoryRuns())
gateway.run()

clock.advance(minutes=2)                                             # 02:01 in La Paz
gateway.wait()

assert issued == [("tx-1", "cron-payments.ReconcileTransactions"), ("tx-2", "cron-payments.ReconcileTransactions")]
assert gateway.status()["cron-payments.ReconcileTransactions"].last_run.outcome == RunOutcome.SUCCEEDED
```

Every bus the cron calls sees the tick as its context — `cron` and `scheduled_for` — and a
failure, even one inside a bus, is logged once by the registry, with `failed_in` naming the bus.

## One run per tick

Before a cron runs, its tick is **claimed** in `runs`: the first claim of `(name, scheduled_for)`
wins, every other returns without running. Every replica may tick — no leader election, no
singleton pod.

| `runs` | For |
|---|---|
| `InMemoryRuns()` | one process: development, tests |
| `DatabaseRuns(database, cron_run_table(metadata))` | every replica, and across restarts — the table is the project's, created by its migrations |

A restarted gateway starts from where each cron last ran, so `missed` sees the ticks that passed
while it was down.

| Policy | Values | Default |
|---|---|---|
| `overlap` | `SKIP`, `ALLOW` | `SKIP` |
| `missed` | `SKIP`, `RUN_LATEST`, `RUN_ALL` (in order) | `RUN_LATEST` |
| `missed_window` | a `timedelta` | 1 day |
| `stale_after` | a `timedelta`: a run unfinished this long past its tick is presumed dead (its replica crashed) and stops holding `overlap=SKIP` | 1 hour |

A tick more than a minute behind the clock counts as missed.

## Seeing it

```python
plan = gateway.plan(until=datetime(2026, 9, 29, tzinfo=UTC))
assert [tick.day for _, tick in plan] == [27, 28]              # the next ticks, before they run

status = gateway.status()["cron-payments.ReconcileTransactions"]
assert status.last_success is not None and status.next_tick.day == 27
```

"No success since X" per cron is the alert that catches a clock that silently stopped.

## Not yet

A Temporal runner (a cron as a Workflow, each bus call an Activity) and a Celery runner, windows
and business calendars, crons defined as data per tenant —
[PRD_08](../prd/PRD_08_scheduling-entrypoint.md).
