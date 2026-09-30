---
name: sincpro-framework-operations
description: Run scheduled work and move schemas with sincpro_framework — the Crons registry per bounded context, CronProcess/CronGateway, one run per tick across replicas, and the Migrations timeline across every context and store. Use whenever a task needs a scheduled job, a cron, a background tick, a database schema change, an Alembic migration, or one command to upgrade/downgrade the whole system.
---

# sincpro-framework-operations

Two things beside the bus: scheduled work, and schema changes. Neither is a use case.

## Crons — callers of use cases on a clock

A cron is a **caller**, like an RPC method: a class in a registry of its own per bounded context, the
buses it orchestrates injected by their own names. It is not a use case and not on a bus.

```python
from sincpro_framework.cron import Cron as _Cron, CronGateway, Crons, Missed, Overlap, Tick


class CronDependencyContextType:                 # dependencies.py
    cybersource: UseFramework


class Cron(_Cron, CronDependencyContextType):    # framework.py
    pass


cron_payments = Crons[CronDependencyContextType]("cron-payments")
cron_payments.add_dependency("cybersource", cybersource)


@cron_payments.cron("0 2 * * *", timezone="America/La_Paz", overlap=Overlap.SKIP, missed=Missed.RUN_LATEST)
class ReconcileTransactions(Cron):
    def run(self, tick: Tick) -> None:
        pending = self.cybersource(QueryPending(day=tick.scheduled_for), ResponsePending)
        for transaction in pending.transactions:
            if tick.once(transaction):           # this one, once per tick
                ...
```

Run them in a background process beside a service (`CronProcess(build_crons).start()`, a module-level
`build_crons` because it runs in the child) or as a deployment of their own (`CronGateway([...]).run()`,
blocking). The gateway looks every 5 s (+ jitter), claims each tick in `runs` before running it (one
run per tick across replicas with a shared `CronRuns`), and gives each run a thread of its own.
`gateway.plan(until)`, `gateway.status()` and `run_now(Cron)` see it; `ManualClock` tests it. A
`replaces=` cron runs instead of the one it names, under the replaced name; `crons.without(Cron)`
switches one off.

Full detail: [references/crons.md](references/crons.md), `docs/cron/README.md`, PRD_08.

## Migrations — every context and store, one timeline

A bounded context owns its data, and that is not always one database. `sincpro_framework.migrations`
orchestrates every chain of every context and store into one timeline: in what order they run, where
the whole system stands, how to put it back. The core needs no database; Alembic for SQL is behind
`[migrations]`.

```python
from sincpro_framework.migrations import ContextMigrations, Migrations, command_line
from sincpro_framework.orm.migrations import AlembicEngine

billing_migrations = ContextMigrations("billing", Path("domains/billing/entrypoints/migrations"))
billing_migrations.store("main", AlembicEngine(billing_tables, database))

migrations = Migrations([common_migrations, billing_migrations])   # entrypoints/migrations.py
raise SystemExit(command_line(migrations))                         # status, upgrade, downgrade …
```

The Makefile calls `python -m myapp.entrypoints.migrations upgrade|status|revision|hash|downgrade|
check|resolve|adopt`. Writing a step is `revision` → read and adjust the body → `hash` → `upgrade`.
A step body imports only `sqlalchemy`/`alembic`, never the project's models. `requires=` names steps
of other chains that must run first; the timeline merges the chains. `downgrade --to` reverts every
later applied step, newest first.

Full detail: [references/migrations.md](references/migrations.md), `docs/migrations/README.md`, PRD_05.

## References

- [references/crons.md](references/crons.md) — registry, schedule, policies, `CronProcess`/`CronGateway`, `runs`
- [references/migrations.md](references/migrations.md) — contexts/stores, `revision`/`hash`/`upgrade`, timeline, `check`, engines, adopt

## Related

- A cron calls the bus; reuse is a Command on the bus (`sincpro-framework`)
- `KeyValueRuns` for crons across replicas: `sincpro-framework-caching`
- Workflows refreshed by a cron: `sincpro-framework-analytics`
