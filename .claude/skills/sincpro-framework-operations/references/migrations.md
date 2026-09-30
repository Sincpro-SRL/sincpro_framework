# Migrations

Depth: `docs/migrations/README.md`, PRD_05. Every block there runs as a test.

`sincpro_framework.migrations` orchestrates the chains of every context and store into one timeline.
It is opt-in twice: a project may migrate however it likes and never import it, or use it and plug
its own engine into any store. The core needs no database; Alembic for SQL is `[migrations]`.

## Where things live

```
domains/<context>/infrastructure/tables.py        # the context's MetaData
domains/<context>/entrypoints/migrations/
  __init__.py              # ContextMigrations("<context>", Path(__file__).parent)
  meta_migration.json      # written by the framework — never by hand
  main/                    # one folder of step bodies per store
entrypoints/migrations.py  # the composition root: every context, and the command line
```

## Declare

```python
from sincpro_framework.migrations import ContextMigrations, Migrations
from sincpro_framework.orm.migrations import AlembicEngine

common_migrations = ContextMigrations("common", Path("domains/common/entrypoints/migrations"))
common_migrations.store("main", AlembicEngine(common_tables, database))
billing_migrations = ContextMigrations("billing", Path("domains/billing/entrypoints/migrations"))
billing_migrations.store("main", AlembicEngine(billing_tables, database))

migrations = Migrations([common_migrations, billing_migrations])
```

- Each store is one **chain**, keeping its own position in the store itself.
- A foreign key into another context names that context's column (`ForeignKey(partner.c.id)`), not
  the string `"partner.id"`.
- The composition root ends with `raise SystemExit(command_line(migrations))`.

## Makefile / command line

```
python -m myapp.entrypoints.migrations upgrade|status|revision <ctx> <store> -m "…"|hash|downgrade --to <id>|check|resolve <ctx> <store> [--at <id>]|adopt <ctx> <store>
```

The system is down while it migrates: `make migrate`, then `make run`.

## Writing a step

`revision` (gives a UUIDv7 id, autogenerates the body) → read and adjust → `hash` (prints the steps
it approved) → `upgrade`. `requires` names steps of other chains that must run first
(`context/store/id`).

- **A step body imports only `sqlalchemy` and `alembic`** — never the project's models or types,
  which change while the step must run as written. A `TypeDecorator` (`JsonText`) is written as its
  `impl` (`sa.Text()`); a type with no `impl` is mapped with `render_types`.
- `upgrade`/`downgrade` refuse a body that changed since it was hashed; `hash` refuses an applied
  step. An applied step is never edited — a new step changes what it did.
- Large backfills are **Commands on the bus**, run as a job — not steps.

## The timeline

Every chain merged: a step goes after what it `requires`, and among the steps free to go the oldest
id goes first. `status()` gives each chain a `ChainState`: `UP_TO_DATE`, `BEHIND` (upgrade applies),
`AHEAD` (refused — downgrade with the newer release, or `resolve`), `DIRTY` (a step on a
non-transactional store failed part-way — refused until `resolve`).

```python
status = migrations.status()
assert {chain.state for chain in status.chains.values()} == {ChainState.UP_TO_DATE}
```

## Back to a point

```python
migrations.downgrade_plan(to=step.id)    # what it would revert; nothing runs
migrations.downgrade(to=step.id)         # reverts every later applied step, newest first
```

`downgrade --to` puts the whole system back to a step that is applied. A step created
`irreversible=True` refuses the downgrade before anything runs (its revert would lose data) —
restore the backup instead. `upgrade_plan`/`downgrade_plan` (`--plan`) answer before running.

## CI: `check` and `hash`

```python
assert migrations.check() == []
```

`check` is about the code: a step body edited since hashed; a non-linear chain; a store the code does
not register; an unknown/circular `requires`; drift for an up-to-date store (`column partner.tax_id
is declared but no step adds it`).

## Another store

An engine is one abstract class (`MigrationEngine`): `position`, `record`, `apply`, `revert`,
`scaffold`, optional `drift`, and `transactional`. A store without transactional DDL declares
`transactional = False` and never retries on its own.

## A database that already has its tables — `adopt`

1. Write the baseline against an **empty** store (autogenerate compares with the store as it stands).
2. Register the context on its real database (it is *behind*); `adopt` records it on the chain's last
   step once the engine reports no drift. Then `check`.
3. Drop the old tool's version table once every database is adopted.

## Guidelines

- A step never imports domain or ORM models.
- Prefer rolling forward; mark `irreversible` when a revert would lose data.
- A store that cannot change in place rebuilds and switches an alias.
