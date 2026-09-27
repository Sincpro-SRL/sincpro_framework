# PRD_05: Migrations across bounded contexts and stores

- **Status**: phase 1 implemented — see `docs/migrations/`. Differs from this spec: there is no
  *late* state — each chain's own order is authoritative, so a step merged late runs in its
  chain's place, and order across chains matters only through `requires`; step bodies live in one
  folder per store (`<store>/`), not per engine; the command line is the project's composition
  root (`python -m myapp.entrypoints.migrations …`), not a global `sincpro-migrate`; `hash`
  re-checksums reviewed edits and refuses a step its store already applied; `AlembicEngine` keeps a
  dirty mark
  in `<version table>_dirty` on databases without transactional DDL (SQLite, MySQL); *ahead* and
  *diverged* are one state — a store on a step the code does not have — since a step merged
  late keeps an old id and the two cannot be told apart; `command_line(migrations)` is a function
  of the entrypoint, so the orchestrator does not depend on it.
- **Changes a decision**: `docs/persistence/reference.md` §3 says migrations "are not in the
  framework, and will not be". This keeps half of it — **the project still writes every step** —
  and moves the other half in: **the orchestration**. Needs an entry in `decisions.md` when built.
- **Opt-in, twice**: a project may ignore all of this and migrate however it likes; or use the
  orchestrator and plug its own engine into any store. Alembic is the one engine the framework
  ships, for SQL stores — it is SQLAlchemy's own migration tool, so shipping it marries nothing new.

## Problem

A bounded context owns its data, and that is not always one database: a `chat` context may keep
its accounts in Postgres, its messages in MongoDB and its timeline in Cassandra. Contexts also
share databases, and a context above can reference a table of `common`. So there are many chains
of migrations, and nothing says in what order they run, where the system stands, or how to put it
back. Every project writes that orchestration itself, and the one that exists shows the failures
(`sincpro_synthesis`, read-only review): an `alembic.ini` section per context and one context
(`common`) with revisions but no section — they never run; random revision ids that say nothing
about order; no check that tables and migrations agree.

The problem is the **orchestration**, not the single migration: Alembic, mongock, cqlmigrate each
run their own chain well.

## Goals

1. A project declares its contexts and each context's stores **once, in code**; the framework
   generates and keeps each context's `meta_migration.json`.
2. One command answers **where the system is** — per chain and as a whole — against what the code
   knows.
3. One command moves the whole system **forward to the end or to a point, and back to a point**,
   across contexts and stores, in a derived order.
4. The core is **engine-agnostic**: an engine is a small abstract class a project can implement
   for any store.
5. CI fails when two branches add steps to the same chain, when an applied step was edited, or
   (for engines that can tell) when the model and the steps disagree.

## Non-goals

- Writing migrations for the project, or a declarative diff-and-apply (Atlas-style).
- Zero-downtime / online migration: the system is **down** while it migrates —
  `make migrate`, then `make run`. So no locks, no expand/contract requirement.
- Transactions across stores. No tool has them ([Flamingock](https://docs.flamingock.io/resources/faq));
  the design resumes instead.
- Replacing Alembic, or shipping an engine for a non-SQL store.

## What the evidence says

| Finding | Consequence here |
|---|---|
| Alembic `depends_on` across branches corrupts `alembic_version` on downgrade ([#789](https://github.com/sqlalchemy/alembic/issues/789), [#1373](https://github.com/sqlalchemy/alembic/issues/1373)); per-module rollback with branch labels rolls back other modules ([example](https://github.com/antosubash/simple_module_python/issues/333)) | **One linear chain and one version table per (context × store)**; never Alembic branches |
| Separate history tables are not separate namespaces ([EF Core #29015](https://github.com/dotnet/efcore/issues/29015)) | A step is named by `(context, store, id)` |
| UUIDv7 orders by wall clock, not by cause ([RFC 9562 §6.2](https://datatracker.ietf.org/doc/rfc9562/)) | Order is `requires` edges first, UUIDv7 as the tie-breaker |
| A lower version merged after higher ones were applied: Flyway "ignored", Rails rolls back the wrong one ([Flyway](https://documentation.red-gate.com/fd/flyway-out-of-order-setting-277579015.html), [rails#16618](https://github.com/rails/rails/issues/16618)) | Order is never the id alone: each chain's own order is authoritative, and across chains `requires` — so a step merged late runs, and reverts, in its chain's place |
| Parallel branches produce two heads; Django rejected rebase as error-prone, `django-linear-migrations` turns it into a git conflict ([ticket 28535](https://code.djangoproject.com/ticket/28535), [adamj](https://adamj.eu/tech/2020/12/10/introducing-django-linear-migrations/)); `atlas.sum` does the same ([Atlas](https://atlasgo.io/concepts/migration-directory-integrity)) | `meta_migration.json` carries a hash per step and a directory hash — two branches adding a step to one chain conflict in git |
| Edited-after-applied steps; checksum algorithm changes invalidated everything ([Flyway](https://github.com/flyway/flyway/issues/2255), [Liquibase 4.22](https://support.liquibase.com/hc/en-us/articles/29383026357531)) | Checksum per step, line endings normalised, algorithm versioned (`v1:…`) |
| Down scripts cannot restore dropped data and are rarely run ([Atlas](https://atlasgo.io/blog/2024/04/01/migrate-down), [Flyway undo](https://documentation.red-gate.com/fd/undo-277578896.html)) | A step may declare itself **irreversible**; a downgrade that would cross it is refused before anything runs — restore a backup instead |
| Old code on a newer schema fails with "Can't locate revision" ([soliplex](https://github.com/soliplex/soliplex/issues/1372), [airflow](https://github.com/apache/airflow/discussions/40193)) | Status **ahead**: refuse, name the unknown steps, say "downgrade with the newer release" |
| Non-transactional failure leaves a "dirty" store ([golang-migrate](https://atlasgo.io/blog/2025/04/06/atlas-and-golang-migrate), [Mongock](https://docs.mongock.io/v5/migration/)) | Mark **dirty** before a non-transactional step; stop; resume or `resolve` by hand — never an automatic revert |
| Every tool keeps history in the store it migrates; none keeps a central ledger | **Each store records its own position**; the global position is computed |
| Data migrations that import live models break later ([Django](https://docs.djangoproject.com/en/5.0/howto/writing-migrations/), [GitLab](https://docs.gitlab.com/development/migration_style_guide/)) | Guideline: steps never import domain or ORM models; large backfills are Commands on the bus |
| Stores that cannot alter in place (Elasticsearch) rebuild and switch an alias ([Elastic](https://www.elastic.co/blog/changing-mapping-with-zero-downtime)) | Guideline for such engines: revert = switch back, valid while the old index exists |

The shape borrows Django's split — loader, graph, executor, recorder
([django/db/migrations](https://github.com/django/django/tree/main/django/db/migrations)) — and
golang-migrate's single driver a third party implements
([database/driver.go](https://github.com/golang-migrate/migrate/blob/master/database/driver.go)).

## The model

A **chain** is one `(context, store)`: a linear list of **steps**, each with a UUIDv7 id, its
parent, a message, a checksum, optional `requires` (steps of other chains it needs) and an
`irreversible` flag.

```python
# domains/chat/entrypoints/migrations/__init__.py
chat_migrations = ContextMigrations("chat")
chat_migrations.store("main", AlembicEngine(tables.metadata, main_database))    # shipped
chat_migrations.store("messages", MongoEngine(mongo_client))                    # the project's

# entrypoints/migrations.py — the composition root: the explicit list
migrations = Migrations([common_migrations, billing_migrations, chat_migrations])

if __name__ == "__main__":
    raise SystemExit(migrations.command_line())
```

Recommended layout per context:

```
domains/<context>/entrypoints/migrations/
  __init__.py              # ContextMigrations of the context
  meta_migration.json      # generated — never edited by hand
  alembic/                 # the Alembic engine's bodies, one per step
  mongo/                   # another engine's, when the context has that store
```

`meta_migration.json`, written by `revision`:

```json
{
  "format": 1,
  "context": "chat",
  "stores": {
    "main": {
      "engine": "alembic",
      "steps": [
        {"id": "0192f3a1…", "parent": null, "message": "create account", "checksum": "v1:9f2c…"},
        {"id": "0192f7c4…", "parent": "0192f3a1…", "message": "add handle", "checksum": "v1:41aa…",
         "requires": ["common/main/0192f1b0…"]}
      ]
    },
    "messages": {"engine": "mongo", "steps": []}
  },
  "sum": "v1:c07e…"
}
```

## The timeline

Every chain of every context is merged into **one timeline**: a topological order of the steps
where each chain keeps its own order, `requires` edges are honoured, and among steps free to go,
the smallest UUIDv7 goes first. With A {1, 2, 5, 6, 7} and B {3, 4, 8} on one database, the
timeline is 1 … 8. Chains on separate stores interleave the same way; nothing depends on it unless
a `requires` says so, and `--context` / `--store` run a subset.

## Where the system is

Each engine answers its chain's **position**: the head it has applied, and whether it is dirty.
The orchestrator compares it with the steps the code knows:

| State | Meaning | What happens |
|---|---|---|
| up to date | the store's head is the chain's last step | nothing |
| behind | the code has steps the store has not applied | `upgrade` applies them |
| ahead | the store has a head the code does not know — older code on a newer store | refuse; name the steps; "downgrade with the newer release" |
| diverged | the store's head is not on the chain the code has | refuse; name both |
| dirty | a non-transactional step failed part-way | refuse; `resolve` after a human looked |

## Commands

| | |
|---|---|
| `status` | every chain's state, and the timeline with the current position marked |
| `upgrade [--to ID]` | apply pending steps in timeline order, to the end or to a step; stop at the first failure |
| `downgrade --to ID` | revert every applied step after `ID`, timeline reversed; refused before anything runs if one is irreversible |
| `revision CONTEXT STORE -m MESSAGE [--requires …] [--irreversible]` | UUIDv7 id, the engine writes the body, `meta_migration.json` is updated |
| `check` | CI: sums match, one linear chain each, `requires` resolve without a cycle, and the engine's drift check where it has one |
| `resolve CONTEXT STORE --at ID` | after a human fixed a dirty store: record where it really is |

```
make migrate      # python -m myapp.entrypoints.migrations upgrade — refuses on ahead / diverged / dirty
make run
```

## The API

Concrete, engine-agnostic, in the framework:

| | |
|---|---|
| `ContextMigrations(name)` | a context's registry; `.store(name, engine)` |
| `Migrations(contexts)` | the orchestrator: `status`, `upgrade`, `downgrade`, `revision`, `check`, `resolve`, `command_line` |
| `Step`, `Position`, `ChainState` | the vocabulary |

The one port a project implements for a store:

```python
class MigrationEngine(ABC):
    name: str                                   # "alembic", "mongo" — written in the manifest
    transactional: bool                         # False: mark dirty before each step

    @abstractmethod
    def position(self, chain: Chain) -> Position: ...          # applied head + dirty, read from the store
    @abstractmethod
    def record(self, chain: Chain, position: Position) -> None: ...
    @abstractmethod
    def apply(self, chain: Chain, step: Step) -> None: ...
    @abstractmethod
    def revert(self, chain: Chain, step: Step) -> None: ...
    @abstractmethod
    def scaffold(self, chain: Chain, step: Step) -> Path: ...  # writes the new step's body
    def drift(self, chain: Chain) -> list[str] | None: ...     # optional; None: cannot tell
```

`AlembicEngine(metadata, database)` implements it: a version table per chain
(`alembic_version_<context>_<store>`), the framework's own `env.py` (no `alembic.ini`),
`rev_id` = the UUIDv7, autogenerate scoped to the context's tables, `drift` from Alembic's
compare. Alembic has no dirty flag; the engine records it for dialects without transactional DDL.

## Guidelines shipped with it

- A step never imports domain or ORM models; large backfills are Commands on the bus, run as a job.
- Prefer roll-forward; mark a step `irreversible` when its revert would lose data, and back up
  before a release that has one.
- Revert with the release that has the steps; then deploy the older one.
- An engine for a store with non-transactional DDL marks dirty and never retries on its own.

## Phases

1. `ContextMigrations`, `Migrations`, the timeline, `status` / `upgrade` / `downgrade` /
   `revision` / `check` / `resolve`, `meta_migration.json` with sums, the CLI, `AlembicEngine`.
2. Drift in `check` with an allowlist; testing helpers (`assert_migrations_match_tables`,
   `assert_up_down_up`); the framework's ecosystem tests migrate through `Migrations.upgrade()`.
3. A guide for writing an engine, with a contract test suite an engine runs against itself.
