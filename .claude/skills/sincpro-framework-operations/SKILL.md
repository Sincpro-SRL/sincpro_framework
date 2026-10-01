---
name: sincpro-framework-operations
description: Run scheduled work and move schemas with sincpro_framework — the Crons registry per bounded context, CronProcess/CronGateway, one run per tick across replicas, and the Migrations timeline across every context and store. Use whenever a task needs a scheduled job, a cron, a background tick, a database schema change, an Alembic migration, or one command to upgrade/downgrade the whole system.
---

# sincpro-framework-operations

Two things that live beside the buses of a service, never on them: **crons** (scheduled callers of
use cases) and **migrations** (schema changes of every store, in one order). Neither is a use case.
This skill stands alone; the deep docs it names live in the framework repository, not in the package.

## Context

- **Crons** answer "run this use case at 02:00 La Paz time, once, even with three replicas". A cron
  is a *caller*, like an RPC method: it decides what to execute and executes Commands on the buses
  it is given. It is not a Feature, not a job queue, not a workflow engine and not a distributed
  lock. Work triggered by something that happened is a domain event; a one-off long job is a
  Command run once.
- **Migrations** answer "in what order do the schema changes of every context and store run, where
  does the whole system stand, and how do I put it back". It runs with the system down, as one
  command. It is not for data backfills (those are Commands on the bus, run as a job), and a
  service with no store of its own does not need it. A project may migrate however it likes and
  never import it.

## Abstractions

| Term | What it is | Kind | Import |
|---|---|---|---|
| `Crons` | The crons of one bounded context: a name (log/trace identity), dependencies by name, `@cron`, `without`; refuses changes once built | registry | `from sincpro_framework.cron import Crons` |
| `Cron` | Base class of a cron: `run(self, tick)`; every registry dependency is set on it as an attribute | port (abstract) | `from sincpro_framework.cron import Cron` |
| `@crons.cron(...)` | Registers a class: `"0 2 * * *"` + `timezone=`, or `every=timedelta(...)`; `overlap`, `missed`, `missed_window`, `stale_after`, `replaces`, `name` | decorator | method of `Crons` |
| `Overlap` / `Missed` | Policies: previous run still going (`SKIP`/`ALLOW`); ticks missed while down (`SKIP`/`RUN_LATEST`/`RUN_ALL`) | setting | `from sincpro_framework.cron import Overlap, Missed` |
| `Tick` | The run in progress: `cron`, `scheduled_for`, `once(key)` | DTO (frozen dataclass) | `from sincpro_framework.cron import Tick` |
| `tick.once(key)` | `True` the first time `key` is asked for this tick, on any replica — an item-level, at-most-once guard | function | method of `Tick` |
| `CronGateway` | The orchestrator: looks every 5 s, claims each due tick, runs each in its own thread; `run`, `stop`, `wait`, `plan`, `status`, `run_now` | adapter (entrypoint) | `from sincpro_framework.cron import CronGateway` |
| `CronProcess` | Runs a gateway in a spawned child process beside the service; `start`, `stop`, `is_alive` | adapter (entrypoint) | `from sincpro_framework.cron import CronProcess` |
| `CronRuns` | The record of runs and the once-per-tick claim: `claim`, `finish`, `running`, `last`, `last_success` | port (abstract) | `from sincpro_framework.cron import CronRuns` |
| `InMemoryRuns` | Default `CronRuns`, in the process — one replica only | adapter | `from sincpro_framework.cron import InMemoryRuns` |
| `KeyValueRuns` | `CronRuns` over any `KeyValueStore` (Redis/Valkey/Memcached) — shared by replicas | adapter | `from sincpro_framework.cron import KeyValueRuns` |
| `Run` / `RunOutcome` / `CronStatus` | One claimed tick / `SUCCEEDED`, `FAILED`, `SKIPPED` / `last_run`, `last_success`, `next_tick` | DTO | `from sincpro_framework.cron import Run, RunOutcome, CronStatus` |
| `ManualClock` | A clock that moves when the test says (`advance(minutes=2)`) | adapter | `from sincpro_framework.testing import ManualClock` |
| `ContextMigrations` | What one context migrates: its name, its folder, one engine per store | registry | `from sincpro_framework.migrations import ContextMigrations` |
| `Chain` | One (context × store): steps run in order, position kept in the store itself | DTO (frozen dataclass) | `from sincpro_framework.migrations import Chain` |
| `Step` | One migration: UUIDv7 `id`, `parent`, `requires` (`context/store/id`), `irreversible`, `checksum` | DTO (frozen dataclass) | `from sincpro_framework.migrations import Step` |
| `Migrations` | The composition root: every context merged into one timeline; `status`, `upgrade`, `downgrade`, `revision`, `hash`, `check`, `resolve`, `adopt`, `*_plan` | registry | `from sincpro_framework.migrations import Migrations` |
| Timeline | `migrations.status().timeline`: every step of every chain in run order, with whether it is applied | function | — |
| `command_line` | The CLI the Makefile calls; exit 1 on refusal, failure or a `check` problem | function | `from sincpro_framework.migrations import command_line` |
| `MigrationEngine` | What a store implements: `position`, `record`, `apply`, `revert`, `scaffold`, optional `drift`, `transactional` | port (abstract) | `from sincpro_framework.migrations import MigrationEngine` |
| `AlembicEngine` | SQL stores: autogenerates from the context's `MetaData`, one version table per chain | adapter | `from sincpro_framework.orm.migrations import AlembicEngine` (extra `[migrations]`) |
| `InMemoryEngine` | A store in memory — tests and learning | adapter | `from sincpro_framework.migrations import InMemoryEngine` |
| `ChainState` / `ChainStatus` / `MigrationStatus` / `Position` | `UP_TO_DATE`/`BEHIND`/`AHEAD`/`DIRTY`; per-chain status; whole status; `head` + `dirty` | DTO | `from sincpro_framework.migrations import ...` |

Look-alikes: the framework's `Cron` (abstract) vs the context's own `Cron(_Cron,
CronDependencyContextType)` base — import the first as `_Cron`. `CronDependencyContextType` is a
class *you* write: annotations only, one per bus or adapter the crons call. The once-per-**tick**
claim is automatic (the gateway does it); `tick.once(key)` is the per-**item** guard you call.
There is no lock or leader election: "one run per tick" is the first successful `claim` in a shared
`CronRuns`. A **chain** is one store's linear history; the **timeline** is all chains merged.
`MigrationRefused` means nothing ran; `MigrationFailed` means a step failed and the ones before it
stay applied.

## Architecture

**In the framework.** `sincpro_framework.cron` (`domain/` Cron, Tick, policies, `CronRuns` port;
`adapters/` InMemoryRuns, KeyValueRuns, clocks; `registry` Crons; `entrypoint/` CronGateway,
CronProcess) needs only the stdlib. `sincpro_framework.migrations` (`domain/` Chain, Step, engine
port; `adapters/` InMemoryEngine; `manifest` meta_migration.json; `orchestrator` Migrations;
`entrypoint/` command_line) needs no database. Optional extras: `AlembicEngine` in
`sincpro_framework.orm.migrations` (`[migrations]` = sqlalchemy + alembic); the Redis/Valkey or
Memcached store behind `KeyValueRuns` (`sincpro_framework.caching.adapters.redis` /
`.memcached`, extras `[redis]` / `[memcached]`).

**In a consumer service** (one `UseFramework` bus per bounded context, created in
`infrastructure/framework.py` before `services/` is imported):

```
myapp/
  domains/payments/
    __init__.py                    # payments = config_payments_framework(...); then `from . import services`
    infrastructure/
      dependencies.py              # DependencyContextType, CronDependencyContextType
      framework.py                 # the bus factory, base Feature/ApplicationService, base Cron
      tables.py                    # the context's MetaData
    services/                      # the Features/ApplicationServices a cron executes
    entrypoints/
      crons.py                     # cron_payments = Crons[...]("cron-payments"); @cron_payments.cron classes
      migrations/
        __init__.py                # payments_migrations = ContextMigrations("payments", Path(__file__).parent)
        meta_migration.json        # written by revision/hash, never by hand
        main/                      # step bodies of the store "main"
  entrypoints/
    crons.py                       # def build_crons() -> CronGateway  (module level)
    migrations.py                  # migrations = Migrations([...]); raise SystemExit(command_line(migrations))
```

**One cron tick:**

```
CronProcess child ─ CronGateway.look(now)            every look_every (+ jitter)
  → due ticks of each cron (trigger, missed, missed_window)
  → runs.claim(name, scheduled_for) ── False: another replica has it → nothing
  → overlap=SKIP and a run still going → recorded SKIPPED
  → own thread: Crons.execute → span "cron", context {cron, scheduled_for}
      → Cron.run(tick) → self.<bus>(Command) → Feature / ApplicationService
  → runs.finish(outcome)        a failure is logged once, never raised to the clock
```

**One upgrade:**

```
make migrate → python -m myapp.entrypoints.migrations upgrade
  → checksums, linear chains, requires valid?        no → refused, nothing runs
  → engine.position() of every store → AHEAD/DIRTY?  yes → refused
  → timeline: each chain's order, `requires` first, oldest UUIDv7 among the free
  → engine.apply(step) for each pending step (non-transactional: dirty mark around it)
  → a failing step stops the run; nothing is reverted on its own
```

## Mistakes an agent makes

- **Several replicas on the default `InMemoryRuns`** — every pod that starts `CronProcess` runs
  every tick, with no error. Pass a shared `runs=KeyValueRuns(store)` (or your own `CronRuns`), or
  run the crons as one deployment with one replica.
- **Trusting `missed=` across a restart with `InMemoryRuns`** — a restart starts from now, so a tick
  that fell during a deploy never runs. Only shared runs let the gateway start from the last run.
- **Logic in `Cron.run`** (repositories, adapters, HTTP) — nothing else can reuse it and it has no
  use-case span. `run` decides and executes Commands on its injected buses.
- **State on `self` in a cron** — one instance serves every tick, from several threads. Keep state
  in locals.
- **`tick.once(key)` assumed to retry** — it is at most once: a step that fails after `once` is not
  retried by another run of the same tick. Call it right before the side effect, keyed per item.
- **`every=timedelta(minutes=15)` expected to count from start** — it counts from the Unix epoch
  (:00, :15, :30, :45 UTC on every replica).
- **A scheduler of your own** (APScheduler, a `while True: sleep` thread, `CronGateway.run()` on
  the web process's main thread) — use `CronProcess(build_crons).start()` with a module-level
  `build_crons`; the child is spawned and builds its own buses.
- **A relative `Path("domains/x/entrypoints/migrations")`** — run from another directory, no
  manifest is found: `status` shows no steps and `revision` writes a manifest in the wrong place.
  Use `Path(__file__).parent` in the context's `migrations/__init__.py`.
- **A step body importing project models or types** (`JsonText`, an Enum, a table) — it works
  today and breaks when the model changes. A body imports only `sqlalchemy` and `alembic`.
- **`upgrade` at service startup in every replica, or a table removed from `MetaData` expecting a
  drop** — the orchestrator takes no lock (run it once, system down, before `make run`), and
  autogenerate never drops a table for you: write the drop in a step.

## Crons — how

```python
from datetime import timedelta

from sincpro_framework import UseFramework
from sincpro_framework.cron import Cron as _Cron, Crons, Missed, Overlap, Tick


class CronDependencyContextType:                 # infrastructure/dependencies.py
    cybersource: UseFramework
    siat_soap_sdk: UseFramework


class Cron(_Cron, CronDependencyContextType):    # infrastructure/framework.py
    """Base cron of this bounded context — typed dependencies included."""


cron_payments = Crons[CronDependencyContextType]("cron-payments")    # entrypoints/crons.py
cron_payments.add_dependency("cybersource", cybersource)
cron_payments.add_dependency("siat_soap_sdk", siat_soap_sdk)


@cron_payments.cron("0 2 * * *", timezone="America/La_Paz", overlap=Overlap.SKIP,
                    missed=Missed.RUN_LATEST, missed_window=timedelta(days=1))
class ReconcileTransactions(Cron):
    def run(self, tick: Tick) -> None:
        pending = self.cybersource(QueryPending(day=tick.scheduled_for), ResponsePending)
        for transaction_id in pending.transactions:
            if tick.once(transaction_id):        # this item, once per tick
                self.siat_soap_sdk(CommandIssueInvoice(transaction_id=transaction_id))
```

Run beside a service: `crons = CronProcess(build_crons).start()` at startup, `crons.stop()` at
shutdown. As a deployment of its own: `CronGateway([cron_payments], runs=...).run()` blocks. The
cron's name is `<registry>.<Class>` (`cron-payments.ReconcileTransactions`). A cron declaring a
dependency the registry lacks fails when the gateway is built, not at 02:00. Full detail:
[references/crons.md](references/crons.md); deep doc in the framework repo: `docs/cron/README.md`,
PRD_08.

## Migrations — how

```python
from pathlib import Path

from sincpro_framework.migrations import ContextMigrations, Migrations, command_line
from sincpro_framework.orm.migrations import AlembicEngine

# domains/billing/entrypoints/migrations/__init__.py
billing_migrations = ContextMigrations("billing", Path(__file__).parent)
billing_migrations.store("main", AlembicEngine(billing_tables, database))

# entrypoints/migrations.py
migrations = Migrations([common_migrations, billing_migrations])
if __name__ == "__main__":
    raise SystemExit(command_line(migrations))
```

The Makefile calls `python -m myapp.entrypoints.migrations upgrade|status|revision <ctx> <store>
-m "..."|hash|downgrade --to <id>|check|resolve <ctx> <store> [--at <id>]|adopt <ctx> <store>`
(`--plan` on upgrade/downgrade runs nothing). Writing a step: `revision` → read and adjust the
body → `hash` → `upgrade`. `requires` names steps of other chains that must run first.
`downgrade --to` reverts every later applied step across all contexts, newest first. CI migrates
a fresh store, then `check`. Full detail: [references/migrations.md](references/migrations.md);
deep doc in the framework repo: `docs/migrations/README.md`, PRD_05.

## References

- [references/crons.md](references/crons.md) — registry, schedule, policies, `CronProcess`/`CronGateway`, `runs`, testing
- [references/migrations.md](references/migrations.md) — contexts/stores, `revision`/`hash`/`upgrade`, timeline, `check`, engines, `adopt`

## Related

- A cron executes Commands on the bus; reuse is a Command on the bus (`sincpro-framework`)
- The `KeyValueStore` providers behind `KeyValueRuns`, and idempotent Commands: `sincpro-framework-caching`
- The tables a migration creates: `sincpro-framework-persistence`
- Workflows refreshed by a cron: `sincpro-framework-analytics`
