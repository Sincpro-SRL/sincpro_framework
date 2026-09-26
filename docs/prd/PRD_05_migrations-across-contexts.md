# PRD_05: Migrations across bounded contexts

- **Status**: proposal
- **Changes a decision**: `docs/persistence/reference.md` §3 says migrations "are not in the
  framework, and will not be". This keeps half of it — **the project still writes every revision** —
  and moves the other half in: **the order and the Alembic environment**. Needs an entry in
  `decisions.md` if accepted.
- **Research**: scratchpad `10_migrations.md`

## Problem

Each bounded context owns its tables and usually migrates alone. But contexts also share a
database, and a context above can reference `common/` — and then **order matters** and nothing
says what it is. Every project writes its own Alembic setup, and the one that exists shows the
failures (`sincpro_synthesis`, read-only review):

- revisions of a context that can never run, because it has no config section;
- two contexts on the same database would share one `alembic_version` and overwrite each other;
- autogenerate in a shared database compares every context's tables, not just its own;
- no test that the tables and the migrations agree;
- no constraint naming convention, so SQLite batch migrations work on unnamed constraints.

## Goals

1. A context declares its schema **once**; the same declaration works whether it has its own
   database or shares one.
2. The order is **derived from the context graph** — `common` before whoever builds on it — never
   from file names.
3. One command migrates everything, in order, per database, safely when two deploys race.
4. A test fails when the tables and the migrations disagree.

## Non-goals

- Writing migrations for the project, or a declarative "diff and apply" (Atlas-style).
- Running migrations at application start (a Kubernetes Job / Helm pre-upgrade hook runs them).
- Replacing Alembic.

## Prior art

| | Order | What we take |
|---|---|---|
| Django | per-app migrations with `dependencies` across apps | **order from a declared graph**; per-revision `requires` |
| Odoo | per-module pre/post/end scripts in module-dependency order | the module graph drives order |
| Flyway | separate modules = separate instances and history tables | **one version table per context** |
| Alembic | `version_table` per environment; `depends_on` only inside one database; `alembic check` | the building blocks |
| Rails engines | `isolate_namespace` prefixes tables | optional table prefix per context |

## The model

**One Alembic environment and one version table (`alembic_version_<context>`) per context**,
ordered by the context graph. Branch labels in a single environment were rejected: they break the
moment a context moves to its own database.

```python
# <context>/infrastructure/schema.py
schema = Schema(
    "billing",
    tables.metadata,                       # this context's MetaData, and nothing else
    versions=Path(__file__).parents[1] / "migrations",
    after=("common",),                     # contexts whose migrations run first
)

# the composition root: migrations.py
migrations = Migrations([common.schema, catalog.schema, billing.schema], database_of)

if __name__ == "__main__":
    raise SystemExit(migrations.command_line())
```

`database_of(context) -> Database` answers which database each context lives in — the same
answer the buses use, so one database or four changes nothing here.

## What `Migrations` does

| | |
|---|---|
| `plan(contexts=())` | the order: a topological sort of `after`; refuses a cycle, an unknown context, two contexts owning the same table in one database, a foreign key into a context that is not in `after` or not in the same database |
| `upgrade(contexts=(), target="head")` | per database: take a lock, run each context in order, stop at the first failure |
| `downgrade(context, target="-1")` | one context |
| `revision(context, message)` | autogenerate scoped to that context's own tables |
| `current()` / `drift()` / `sql()` | where each context is; what differs from the tables; the SQL without running it |
| `config_for(context)` | the Alembic `Config`, for anything this does not cover |

The framework ships the `env.py`: it receives the `Schema` directly (no `alembic.ini`), limits
autogenerate to the context's tables, runs each revision in its own transaction, and on SQLite
turns foreign keys off for batch operations and checks them after.

A revision can pin what it needs from another context, Django-style, and the plan checks it:

```python
requires = {"common": "4f1a2b"}      # this revision needs common at least at 4f1a2b
```

## Naming convention

`NAMING_CONVENTION` is exported and used by `entity_table`, so every constraint has a stable name
and autogenerate stops seeing phantom renames. Adopting it on an existing database is one reviewed
revision per context.

## Commands

```
make migrate                  # upgrade every context, in order
make db-revision ctx=billing m="add due date"
make db-check                 # CI: models and migrations agree, one head per context
make db-current / db-downgrade ctx=… / db-sql
```

## Tests a project gets

```python
from sincpro_framework.testing import (
    assert_migrations_match_tables,   # autogenerate finds nothing to do
    assert_one_head_per_context,
    assert_up_down_up,                # every revision reverses
)
```

## Operating it

- **Expand → migrate → contract**: add the new column, backfill, switch the code, remove the old
  one a release later — a rolling deploy runs old and new code on the same schema.
- **Data migrations**: small backfills in the revision, over `sa.table()` snapshots (never live
  models — a revision must still run when the model has changed); large ones as Commands on the
  bus, run as a job.
- **Lock**: a Postgres advisory lock per database (note: it hangs behind PgBouncer in transaction
  pooling — connect directly for migrations).

## Phases

1. `Schema`, `Migrations.plan / upgrade / revision / current`, the shipped `env.py`, one version
   table per context, the lock.
2. `NAMING_CONVENTION`, `db-check`, the test helpers; the framework's own ecosystem tests migrate
   through `Migrations.upgrade()` instead of `create_all`.
3. `requires` per revision; `drift()` and `sql()`.

## Open questions

- Does `after` live on `Schema`, or on a context-graph object the framework does not have yet (the
  same graph PRD_04 addons and `layer_violations` would read)?
- Is a table prefix per context mandatory, optional, or not offered?
- Is the lock the framework's, or the deploy's?
