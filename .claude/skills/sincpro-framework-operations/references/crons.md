# Crons

Depth: `docs/cron/README.md`, PRD_08. Every block there runs as a test.

A cron is a **caller** of use cases, not a use case: a class in a registry of its own per bounded
context, the buses it orchestrates injected by their own names.

## Registry and base

```python
from sincpro_framework.cron import Cron as _Cron, Crons, Missed, Overlap, Tick


class CronDependencyContextType:
    cybersource: UseFramework
    siat_soap_sdk: UseFramework


class Cron(_Cron, CronDependencyContextType):
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
        for transaction in pending.transactions:
            if tick.once(transaction):
                self.siat_soap_sdk(CommandIssueInvoice(transaction_id=transaction))
```

- `@crons.cron("…", timezone=…)` — five fields with `*`, lists, ranges, steps and names. A timezone
  is required; DST is evaluated in it.
- `@crons.cron(every=timedelta(minutes=15))` — interval, counted from the Unix epoch.
- Every dependency of the registry is injected into every cron; `cron_payments.deps.cybersource`
  reaches the same instance from outside. A cron declaring a dependency the registry lacks fails when
  the registry is built, not at 02:00.
- One instance serves every tick: state lives in locals. `tick.once(key)` is `True` the first time
  `key` is asked for this tick, on any replica.
- Every bus the cron calls sees the tick as its context (`cron`, `scheduled_for`); a failure is logged
  once by the registry with `failed_in` naming the bus.

## Running them

**Beside a service, in a process of their own (default):**

```python
from sincpro_framework.cron import CronGateway, CronProcess

def build_crons() -> CronGateway:      # a module-level function: it runs in the child
    return CronGateway([cron_payments])

crons = CronProcess(build_crons).start()   # at startup
crons.stop()                               # at shutdown: finishes the runs in progress
```

The child is spawned, never forked; a bus cannot cross a process.

**As a deployment of its own:**

```python
CronGateway([cron_payments, cron_billing], runs=…).run()   # blocks on the clock
```

Inside, the gateway looks every 5 s (`look_every=`, `jitter=`), claims each due tick in `runs` before
running, and gives each run a thread of its own (`workers=` caps them). The ticks of one cron run in
order.

## One run per tick

Before a cron runs, its tick is claimed in `runs`: the first claim of `(name, scheduled_for)` wins.
`InMemoryRuns` (default) covers one replica. Several replicas need a shared `CronRuns` (abstract:
`claim`, `finish`, `running`, `last`, `last_success`) — `KeyValueRuns(store)` or the project's own.

| Policy | Values | Default |
|---|---|---|
| `overlap` | `SKIP`, `ALLOW` | `SKIP` |
| `missed` | `SKIP`, `RUN_LATEST`, `RUN_ALL` | `RUN_LATEST` |
| `missed_window` | a `timedelta` | 1 day |
| `stale_after` | a run unfinished this long past its tick is presumed dead | 1 hour |

## Seeing, replacing, running now

```python
gateway.plan(until=…)                    # the next ticks, before they run
gateway.status()["cron-payments.ReconcileTransactions"]   # last_success, next_tick
gateway.run_now(ReconcileWithTheBank)    # one run now; claimed and recorded like any tick
```

`replaces=Reconcile` runs the class under the replaced cron's name (history and alerts go on);
`crons.without(Remind)` switches one off. Both refused once the registry is built.

## Testing

`cron_payments` is exercised with a `ManualClock`: advance time, `gateway.wait()`, assert
`gateway.status()[…].last_run.outcome == RunOutcome.SUCCEEDED`.
