# Migrations: every context and store, one timeline, one command

A bounded context owns its data, and that is not always one database: `billing` may live in
Postgres next to `common`, and `chat` may keep messages in MongoDB. Each store has its own chain
of migrations. What no single tool answers is the **orchestration**: in what order the chains
run, where the whole system stands, and how to put it back.

`sincpro_framework.migrations` answers that and nothing else. It is opt-in twice: a project may
migrate however it likes and never import it; or use it and plug its own engine into any store.
The core needs no database — Alembic, for SQL stores, is `sincpro_framework.orm.migrations`,
behind the `[migrations]` extra.

Every block on this page runs, in order, in `tests/docs/test_persistence_guide.py`.

## Where things live

```
domains/<context>/infrastructure/tables.py        # the context's MetaData
domains/<context>/entrypoints/migrations/
  __init__.py              # ContextMigrations("<context>", Path(__file__).parent)
  meta_migration.json      # written by the framework — never by hand
  main/                    # one folder of step bodies per store
  messages/
entrypoints/migrations.py  # the composition root: every context, and the command line
```

A context's folder is `Path(__file__).parent` of its `migrations/__init__.py`, so it never
depends on where the command runs. On this page the folders are relative, because every block
runs in a scratch directory.

## Declare what each context migrates

```python
from pathlib import Path

from sqlalchemy import Column, ForeignKey, Integer, MetaData, String, Table

from sincpro_framework.migrations import ContextMigrations, Migrations
from sincpro_framework.orm import Database
from sincpro_framework.orm.migrations import AlembicEngine

database = Database("sqlite:///erp.sqlite3")

common_tables = MetaData()
partner = Table(
    "partner", common_tables, Column("id", Integer, primary_key=True), Column("name", String(80))
)

billing_tables = MetaData()
Table(
    "invoice",
    billing_tables,
    Column("id", Integer, primary_key=True),
    Column("partner_id", Integer, ForeignKey(partner.c.id)),   # another context: its column
)

common_migrations = ContextMigrations("common", Path("domains/common/entrypoints/migrations"))
common_migrations.store("main", AlembicEngine(common_tables, database))

billing_migrations = ContextMigrations("billing", Path("domains/billing/entrypoints/migrations"))
billing_migrations.store("main", AlembicEngine(billing_tables, database))

migrations = Migrations([common_migrations, billing_migrations])
```

- A context lists its stores; each store is one **chain** — its steps run in order and it keeps
  its own position, in the store itself (`alembic_version_<context>_<store>` for Alembic).
- A foreign key into another context names that context's column (`ForeignKey(partner.c.id)`),
  not the string `"partner.id"`, which SQLAlchemy resolves only inside one `MetaData`.
- The composition root ends with `raise SystemExit(command_line(migrations))`, and the Makefile
  calls it:

```make
MIGRATIONS = python -m myapp.entrypoints.migrations
export SINCPRO_FRAMEWORK_LOG_LEVEL ?= INFO    # one line per step, not every SQL statement

migrate:      ## apply every pending step, in order
	$(MIGRATIONS) upgrade
db-status:
	$(MIGRATIONS) status
db-revision:  ## make db-revision ctx=billing store=main m="add due date" [args="--requires common/main/<id>"]
	$(MIGRATIONS) revision $(ctx) $(store) -m "$(m)" $(args)
db-hash:      ## after writing or reviewing a step body
	$(MIGRATIONS) hash
db-down:      ## make db-down to=<step id, or its start>
	$(MIGRATIONS) downgrade --to $(to)
db-resolve:   ## make db-resolve ctx=chat store=messages [at=<step id>]
	$(MIGRATIONS) resolve $(ctx) $(store) $(if $(at),--at $(at))
db-check:     ## CI, after migrating a fresh store
	$(MIGRATIONS) check
```

The system is down while it migrates: `make migrate`, then `make run`.

## Write steps, move forward

```python
create_partner = migrations.revision("common", "main", "create partner")
migrations.upgrade()                 # autogenerate compares against the store as it stands

create_invoice = migrations.revision("billing", "main", "create invoice", requires=[create_partner.key])
applied = migrations.upgrade()

assert [step.message for step in applied] == ["create invoice"]
assert (migrations.chain("billing", "main").folder / create_invoice.file).exists()
```

`revision` gives the step a UUIDv7 id, lets the store's engine write its body (Alembic
autogenerates it from the context's own tables) and records it in the context's
`meta_migration.json`, with a checksum. `requires` names steps of other chains that must run
first — `context/store/id`, or `--requires` on the command line.

Writing a step is: `revision`, then read and adjust its body, then `hash` — which prints the
steps it approved — then `upgrade`. `upgrade` and `downgrade` refuse a body that changed since
it was hashed, and `hash` refuses a step its store already applied: an applied step is never
edited, a new step changes what it did. Autogenerate needs the store on the chain's last step;
a table removed from the `MetaData` is not dropped for you — write the drop in a step.

## The timeline

Every chain of every context is merged into one order: each chain keeps its own, a step goes
after what it `requires`, and among the steps free to go the oldest id goes first. With
`common` {1, 2, 5} and `billing` {3, 4} on one database, the timeline is 1 … 5.

```python
from sincpro_framework.migrations import ChainState

status = migrations.status()

assert [step.message for step, applied in status.timeline] == ["create partner", "create invoice"]
assert {chain.state for chain in status.chains.values()} == {ChainState.UP_TO_DATE}
```

| State | Meaning | What happens |
|---|---|---|
| up to date | the store stands on the chain's last step | nothing |
| behind | the code has steps the store has not applied | `upgrade` applies them |
| ahead | the store stands on a step this code does not have — a newer release's, or an abandoned branch's | refused: downgrade with the newer release, or `resolve` |
| dirty | a step on a store without transactions failed part-way | refused until `resolve` |

`status` prints each step's full id; any command that takes an id also takes its start, when
only one step begins that way.

## Back to a point

```python
reverted = migrations.downgrade(to=create_partner.id)

assert [step.message for step in reverted] == ["create invoice"]
assert migrations.status().chains["billing/main"].state == ChainState.BEHIND
migrations.upgrade()
```

`downgrade --to` puts the whole system back to that step — one that is applied — reverting every
later applied step, newest first, across every context and store. A step created with `irreversible=True` — its
revert would lose data — refuses the downgrade before anything runs: restore the backup taken
before it instead. Revert with the release that has the steps, then deploy the older one.

## When a step fails

The run stops at the failing step: what ran before it stays, nothing after it runs, and nothing
is reverted on its own. A store with transactions stays where it was, so fixing the step and
running `upgrade` again resumes. A store without them — `transactional = False`, and Alembic on
SQLite or MySQL — is recorded dirty around each step, and a failure part-way leaves it dirty:
every command refuses, naming the step that failed and the `resolve` to run. Look at the store
and undo what the failed step left half done, then `resolve CONTEXT STORE --at <the step before
it>` — or `--at` the failed step, when it did land whole — and `upgrade` again.

## CI: `check` and `hash`

```python
assert migrations.check() == []
```

`check` is about the code — where each store stands is `status`. CI migrates a fresh store,
then checks. It refuses, one sentence each:

- a step body edited since it was hashed — review the edit, then `hash`;
- a chain that is not linear — two branches each added a step: revision one again on top of the
  other (the manifest's `sum` line already made it a git conflict);
- a store in a manifest that the code does not register, or registered with another engine than
  its steps were written for; an unknown or circular `requires`;
- for a store that is up to date, what its engine reports as drift — for Alembic, "column
  partner.tax_id is declared but no step adds it".

## Another store: write its engine

An engine is one abstract class. This one keeps documents' schema versions in a JSON file — the
same shape a MongoDB or Cassandra engine takes, with the position kept in the store itself:

```python
import json

from sincpro_framework.migrations import Chain, MigrationEngine, Position, Step


class JsonFileEngine(MigrationEngine):
    name = "json-file"
    transactional = False                      # a failure part-way leaves the store dirty

    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> dict:
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def position(self, chain: Chain) -> Position:
        raw = self._read().get(chain.key, {"head": None, "dirty": False})
        return Position(raw["head"], raw["dirty"])

    def record(self, chain: Chain, position: Position) -> None:
        state = self._read()
        state[chain.key] = {"head": position.head, "dirty": position.dirty}
        self.path.write_text(json.dumps(state))

    def apply(self, chain: Chain, step: Step) -> None:
        body = json.loads((chain.folder / step.file).read_text())   # a step's body, by its chain
        ...                                                         # run body["up"] on the store
        self.record(chain, Position(step.id))

    def revert(self, chain: Chain, step: Step) -> None:
        ...                                                         # run its "down"
        self.record(chain, Position(step.parent))

    def scaffold(self, chain: Chain, step: Step) -> Path:
        chain.folder.mkdir(parents=True, exist_ok=True)
        body = chain.folder / f"{step.id}.json"
        body.write_text(json.dumps({"message": step.message, "up": [], "down": []}))
        return body


chat_migrations = ContextMigrations("chat", Path("domains/chat/entrypoints/migrations"))
chat_migrations.store("messages", JsonFileEngine(Path("chat-store.json")))
migrations = Migrations([common_migrations, billing_migrations, chat_migrations])

migrations.revision("chat", "messages", "create inbox")
assert [step.message for step in migrations.upgrade()] == ["create inbox"]
```

`step.file` is relative to the chain's folder — `chain.folder / step.file` is the body `scaffold`
wrote. `drift` is optional: an engine that cannot compare the store with the code leaves it out.

## Guidelines

- A step never imports domain or ORM models — they change, and the step must still run as it was
  written. Large backfills are Commands on the bus, run as a job, not steps.
- Prefer rolling forward. Mark a step `irreversible` when its revert would lose data, and take a
  backup before the release that has one.
- A store that cannot change in place (a search index) rebuilds and switches an alias; its revert
  switches back while the old one still exists.
- An engine for a store without transactional DDL declares `transactional = False` and never
  retries on its own.
