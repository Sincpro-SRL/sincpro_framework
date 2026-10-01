# Persistence: the vocabulary, the SQLAlchemy adapter, and what stays in your project

`sincpro_framework.ddd` says what an aggregate is and how it is asked about. `sincpro_framework.orm`
runs that against a database. The first needs nothing installed; the second is the optional extra
`sincpro-framework[sqlalchemy]`, like `[opentelemetry]` and `[rpc]`.

This page is the contract between the two and the project that uses them: what the framework
provides, what every project declares for itself, and what the layer deliberately does not do.
The reference implementation is `sincpro_synthesis`, where the layer was prototyped against a real
catalogue before it moved here.

---

## 1. The vocabulary — `sincpro_framework.ddd`

| Thing | What it is | Where it travels |
|---|---|---|
| `Entity` | The four fields every aggregate carries: `id` (UUID v7, 32 hex chars), `created_at`, `updated_at`, `version` | A dataclass base; a subclass keeps declaring its own fields positionally |
| `Criteria` | What a caller asks: `where`, `order`, `pagination`, `grouping`, `count`, `meta` | A DTO; it is what a URL, a queue or a POST body carries |
| `EntityCollection[T]` | What comes back: the records, the `cursor`, a typed `Count`, what was `dropped`, and the model's `meta` | Frozen; every derived collection drops its page metadata |
| `Meta` | What the model publishes: fields, types, operators, what is sortable | Reaches the client as `model_meta_data` |
| `Query` / `ResponsePaginatedQuery` | The two shapes every read inherits | Command and Response bases |
| `matches(record, expression)` | The filter language evaluated in memory | Tests without a database; `EntityCollection.filtered_by` |
| `Translated`, `translations()` | The words a screen shows: the aggregate names itself, each field declares its own in `field(metadata=…)` | `Meta.name` and `Meta.fields[x].label` / `.help` |
| `DomainEvent`, `Entity.record` / `pull_events` | What happened, said by the aggregate and kept in memory until the Feature pulls it | See [events.md](../events/README.md) |

Three rules, each preventing a failure that was observed in real code:

- **A dropped filter is reported, never silent.** An unknown field or an operator the type does not
  take goes into `EntityCollection.dropped`; a dropped filter *widens* the result, so it is said out loud.
- **A partial collection refuses to fold itself.** `sum_by` over a page raises and names
  `self.repository.measures(...)`, the call that answers about the whole set.
- **Ordering by a nullable column is refused.** A keyset comparison omits rows holding `NULL`, which
  is the one failure in this design that loses data without raising.

`Entity` is a convention, not a requirement: the engine works for any mapped class, and identity is
read from the primary key. What the convention adds is the version check and the stamped
`updated_at` — `created_at` on the insert, the moment of each later write — done by the adapter, `translations()` for a screen, and `record()` for events.

### The class says the words; `Meta` carries them

```python
@dataclass
class Invoice(Entity):
    number: str = field(
        default="",
        metadata={
            "label": {"default": "Number", "es": "Número"},
            "help": {"default": "As printed on the document"},
        },
    )

    @classmethod
    def translations(cls) -> Translated:
        return {"default": "Invoice", "es": "Factura"}
```

**A field's words are declared on the field**, in the `metadata` a dataclass field already
takes, beside its type and its default — so the one place that says what `number` is also says
what to call it. The class method answers only the aggregate's own name.

`Translated` is `dict[str, str]`: a text per locale, with a `default`. `describe()` reads both:

```python
meta.name                     # {"default": "Invoice", "es": "Factura"}
meta.aggregate                # "Invoice", the class name
meta.fields["number"].label   # {"default": "Number", "es": "Número"}
meta.fields["number"].help    # {"default": "As printed on the document"}
```

A class that declares nothing is named after itself and says nothing else. The client reads the
dictionary and picks the locale.

A text in several languages is also a column type, `TranslatedText`: a JSON object on disk, searched with `like`
across every language at once, never ordered.

---

## 2. The adapter — `sincpro_framework.orm`

```python
from sincpro_framework.orm import Database, Repository, entity_table, map_aggregates

database = Database("postgresql+psycopg://…")           # one per bounded context
repository = Repository(database)                     # injected as `self.repository`

page = self.repository.search(Invoice, criteria)                    # EntityCollection: cursor, count, meta, dropped
one = self.repository.get(Invoice, invoice_id)                      # or None
self.repository.save(invoice)                                       # insert if new, update if loaded
with self.repository.context() as repository:                         # several of those, one transaction
    invoice = repository.get(Invoice, invoice_id)
    invoice.confirm()
    repository.save(invoice)
```

| Surface | Reads or writes | Notes |
|---|---|---|
| `search`, `browse`, `get`, `stream` | read | A page, records by id in the order given, one by id, every page in turn |
| `exists`, `first`, `one`, `get_by` | read | The short questions: is there any, the one in front, the only one (refusing zero and two), one by a natural key |
| `pluck`, `distinct` | read | One column of the whole result set, and the values it actually holds — no record is built |
| `export` | read | Every page as dictionaries, one at a time: a report or a CSV without holding the set |
| `pivot` | read | Groups crossed by groups with their margins; four statements whatever the volume |
| `explain` | neither | What the criteria will become, what it dropped and how many statements it costs, without running it |
| `count`, `group_by`, `group_by_levels`, `measures` | read | Answered in SQL over the whole result set, never over a page; `group_by_levels` with a page asked also gives every group the ids of its first page and a cursor |
| `statement` / `run` | read | The escape hatch: a real `Select` out, the usual envelope back in |
| `fetch_all` | read | Every page, as one complete collection — hands a bounded set to the in-memory algebra |
| `save`, `remove` | write | Take the aggregate, whole: the children it owns are written and removed with it ([relations §8](relations.md#8-an-aggregate-is-saved-whole)) |
| `upsert(records, on=, update=)` | write | Insert or overwrite by a key the table holds unique; no version check, `before_save`/`after_save` only |
| `update_all`, `remove_all` | write | Every row a filter matches, one statement, the count back; no hooks or cascade, version raised; a page or an unanswerable condition is refused |
| `archive` | write | Stamps `archived_at` and keeps the row, for an `ArchivableMixin` aggregate; refused for anything else |
| `retrying(work)` | write | Runs a unit of work again on `StaleAggregate` or `TransactionConflict`, and raises the last failure as it was |
| `narrowed(criteria)` | both | The same database seen through a filter nothing can widen: a tenant, a branch, a permission |
| `context(isolation=, read_only=, timeout=, engine=)` | both | The same engine bound to one session; the block is the transaction, configured where it begins ([transactions](transactions.md)) |
| `after_commit`, `after_rollback` | neither | Inside a unit of work: what runs once this transaction committed, or was undone |
| `repository.session`, `flush`, `commit`, `savepoint` | both | Inside a unit of work only: SQLAlchemy whole, a checkpoint, a part that fails on its own |
| `get(…, for_update=True)`, `search(…, for_update=True)` | read | Row locks held until the unit of work commits; `skip_locked` steps over what another worker holds, `nowait` fails at once |

The adapter `sincpro_framework/orm/sqlalchemy/` is in the layers every framework component uses,
one module per responsibility, each named after what it does. A layer imports only the ones
below it, and inside `services/` the workflows orchestrate the atomic services as an
ApplicationService orchestrates Features — `tests/orm/test_layers.py` holds it to both:

| Layer · module | What it is |
|---|---|
| **entrypoint/** `repository.py` | `Repository(UnitOfWork, Reading, Writing)`: the facade a Feature receives as `self.repository` — the typed surface, composed of the workflows |
| **services/workflows/** `store.py` | `Store`: what the workflows share — the database, the session a call runs in, the scope, the transaction |
| **services/workflows/** `unit_of_work.py` | `UnitOfWork`: `context`, `savepoint`, `commit`, `flush`, `after_commit`, `retrying`, `narrowed` |
| **services/workflows/** `reading.py` | `Reading`: every reading — a page, a count, a grouping, a pivot, an export, the short questions |
| **services/workflows/** `writing.py` | `Writing`: `save`, `remove`, `archive`, and the doors named for what they skip — `upsert`, `update_all`, `remove_all` |
| **services/** `data_mapper.py` | the Data Mapper: tables, the mapping call, the `Entity` columns and `Relation`; the aggregate never learns its table |
| **services/** `sql_translator.py` | `Criteria` → `Select`; the grain registry per dialect |
| **services/** `relation_resolver.py` | the database kinds of relation a specification names, once per node for a whole page |
| **services/** `cascade.py` | what `save` and `remove` do with the children a root holds |
| **services/** `upsert.py` | the dialects an upsert speaks, and whether a table holds a key unique |
| **services/** `model_introspection.py` | `describe(cls) → Meta`: asks the mapper what a class looks like |
| **domain/** `relations.py` | how a record keeps its relations and the words a cascade decides by: `Held`, `Orphans` |
| **domain/** `registry.py` | what is mapped: the relations per aggregate, the aggregates per table |
| **domain/** `transaction.py` | what a unit of work is opened with: `Isolation`, `Transaction` |
| **domain/** `custom_fields.py` | column types: `JsonText`, `TranslatedText` |
| **infrastructure/** `database.py` | `Database`: one engine, one session factory, observed from birth |
| **infrastructure/** `transaction_opening.py` | applying a `Transaction` before the first statement |
| **infrastructure/** `engine_errors.py` | the driver's errors named by what happened |
| **infrastructure/** `transaction_hooks.py` | `after_commit` / `after_rollback` per unit of work, savepoints included |
| **infrastructure/** `observability.py` | every statement to the logger, the tracer and the error tracker |
| **infrastructure/** `change_tracking.py` · `read_tracking.py` · `cache_invalidation.py` | what every flush and every read goes through without being asked |

What a project imports is what `sincpro_framework.orm` exports; the paths inside are the
framework's own.

### Criteria is the boundary language; inside, SQLAlchemy is whole

`Criteria` is what HTTP, MCP, a queue or another context sends: small on purpose, serialisable,
validated against the model. It is **not** the language a Feature writes complex logic in. A
reconciliation, a settlement, a posting run reach for `repository.session` and use Core and ORM with no
wrapper:

```python
with self.repository.context() as repository:
    for batch in repository.stream(Line, Criteria(where=…, pagination=Pagination(limit=500))):
        measures = repository.session.execute(select(Line.account_id, func.sum(Line.amount))…)
        for group in reconcile(batch, measures):
            with repository.savepoint():                 # one group fails, the rest stand
                for line in repository.search(Lines, group.criteria, for_update=True):
                    line.reconcile(group)
                    repository.save(line)
        repository.commit()                              # each batch durable on its own
```

What the layer adds around SQLAlchemy is the transaction, the version check, the stamped
`updated_at` and the trace. What it never adds is a translation of joins,
windows or bulk statements into its own vocabulary: those are SQLAlchemy's, and the escape hatch is
the door.

Two races a write can lose, and how they surface:

- **`StaleAggregate`** — the row holds a newer `version` than the one this caller loaded. The fix is to
  read again and decide over the current state, never to retry the same write. SQLAlchemy's
  `version_id_col` does the conditional `UPDATE … WHERE version = :loaded`; `map_aggregates` switches
  it on for every `Entity`.
- **`DuplicateAggregate`** — a unique value already taken (an id, a fingerprint). Reported in the
  layer's own word so a use case resolves the race without importing the driver's exception.

Suggested HTTP mapping: `InvalidCriteria` → 400, `ContractViolation` → 422, both conflicts → 409.

### Declaring a mapping

```python
# infrastructure/tables.py — the only module that knows both the class and the table
mapper_registry = registry()
metadata = mapper_registry.metadata

invoice_table = entity_table(
    "invoice", metadata,
    Column("number", Text, nullable=False),
    Column("total", Numeric, nullable=False),
    Index("invoice_number", "number", unique=True),
)

def apply_mappings() -> None:
    map_aggregates(mapper_registry, {Invoice: invoice_table})
```

Imperative mapping, so `domain/` stays plain dataclasses with no import from the ORM. `entity_table`
prepends the four `Entity` columns; `map_aggregates` is idempotent and sets `version_id_col`. Call
`apply_mappings()` when the bus is built, not on import — nothing the application loads imports
`tables.py` on its own, and an unmapped class answers `NoInspectionAvailable` on the first read.

A table that predates the convention maps its own column names onto the `Entity` attributes:

```python
map_aggregates(registry, {Dataset: dataset_table},
               properties={Dataset: {"id": dataset_table.c.dataset_id}})
```

### Two conventions an aggregate opts into

| Mixin | Columns | What the adapter does |
|---|---|---|
| `AuditedMixin` | `audit_columns()` → `created_by`, `updated_by` | Stamps them on every flush from `Database(url, actor=lambda: bus.current_context().get("user.id"))`; without an actor they stay `None` |
| `ArchivableMixin` | `archive_columns()` → `archived_at` | `archive` stamps it and keeps the row, `remove` deletes, and every reading leaves the archived out unless the criteria names `archived_at` |

```python
@dataclass
class Client(AuditedMixin, ArchivableMixin, Entity):
    name: str

client_table = entity_table(
    "client", metadata, *audit_columns(), *archive_columns(), Column("name", Text, nullable=False)
)
```

### Engines: the provider's choice

`Database(url, **engine_options)` passes everything to `create_engine`: pool size, `pool_pre_ping`,
`connect_args`. The framework picks no driver and no pool.

- **SQLite.** The pragmas a server wants (WAL, busy timeout, foreign keys) and the column types a
  legacy schema needs (ISO text timestamps, lists of dataclasses) are the project's: `sincpro_synthesis`
  keeps both in its own `orm/`. `entity_table(..., datetime_type=...)` takes whatever the project uses.
- **PostgreSQL.** The translator is portable except date grains in `grouping`, which come from a
  per-dialect registry with SQLite and Postgres built in. `contains` over a JSON-as-text column is a
  `LIKE` on the cast text on both engines; a project on `jsonb` that wants `@>` writes it through the
  escape hatch. `for_update` and `skip_locked` are real here and ignored on SQLite.
- **Anything else.** `register_grain_translator("mysql", …)` teaches the grain; the rest is
  SQLAlchemy's own portability. The suite runs against SQLite, which needs nothing installed;
  compile-level tests cover the Postgres date grains. Run the suite against your engine before
  relying on it in production.

### The moments of a write

`database.before_flush(run)`, `after_flush(run)`, `after_commit(run)` and `after_rollback(run)` each
take a `run(session)` and answer the database. They reach every write through that database, a
repository or not; `docs/persistence/lifecycle.md` says which moment is for what.
`invalidate_on_commit(database, cache, *aggregates)` wires a `QueryCache` to them: it lets go of
an aggregate's reads when a write of it commits, and keeps them on a rollback.

### Observability

A `Database` observes itself, through what the framework already has (`observability.py`):

| Output | When | What |
|---|---|---|
| logger, DEBUG | always | one line per statement: operation, table, statement, `duration_ms`, `rows`; failures at ERROR |
| tracer | the process is collecting | one span per statement, `SELECT dataset`, through `sincpro_framework.observability.process` |
| error tracker | a Sentry / GlitchTip DSN is configured | every database failure, under layer `database` |

`Database(url, logger=bus.logger)` sends the queries to the log of the bus that owns the database,
so a Feature can be debugged down to its statements next to its own lines. Without a logger the
layer logs as `sincpro_framework.sql`. **No parameter value reaches a log, a span or a report**:
SQLAlchemy binds values separately, and that is the only shape allowed.

---

Why each piece is the way it is, decision by decision, is `docs/persistence/decisions.md`.
What a read brings back of each record, at any depth, is `docs/persistence/specification.md`.
The end-to-end proof of all of the above is `docs/persistence/testing.md`.

## 3. What every project owns

The framework stops at the engine. These stay in the project, and `sincpro_synthesis` is the
template for each:

| Concern | Where in the project | Reference file |
|---|---|---|
| Which database each bounded context talks to | `common/infrastructure/orm/get_database.py` | one `Database` per context, cached per process, URL from settings with a local SQLite default |
| Injecting the engine | `<ctx>/infrastructure/dependencies.py` | `add_dependency("orm", Repository(get_database("catalog")))` |
| The tables and the mapping call | `<ctx>/infrastructure/tables.py` | `entity_table` + `map_aggregates`, `apply_mappings()` from `config_<ctx>_framework` |
| The migration steps | `<ctx>/entrypoints/migrations/` — its `ContextMigrations`, the generated `meta_migration.json`, one folder of step bodies per store | `entrypoints/migrations.py` as the composition root and CLI, `make migrate` in the Makefile — [migrations/](../migrations/README.md) |
| Wire mapping of the layer's exceptions | the HTTP entrypoint | one table from exception type to status |

**The project writes every migration step; the framework orchestrates them.** A step is a
decision about that project's data, so its body is the project's — autogenerated by Alembic from
the metadata `tables.py` declared, and reviewed. What the framework owns is the order across
contexts and stores, where the system stands, and moving it forward or back as one:
[migrations/](../migrations/README.md). Using it is optional.

---

## 4. Deliberately not provided

What a read brings back, masks, embedded shapes and relations of every kind, is provided: see
`specification.md`.

Each of these has a concrete reason to wait, and none changes the vocabulary above.

- **A message broker, and any event storage.** Events have two in-process buses
  ([events.md](../events/README.md)); a broker is the third implementation of the same API. The framework
  keeps no event table: durability is the project's decision, through its own unit of work.
- **An async engine.** The bus has `AsyncBus`; the engine is synchronous. Add it when a caller is.
- **An abstract `Repository`, not a protocol.** Structural typing checked names only — five methods with the wrong signatures passed `isinstance`. Inheriting is what makes the type checker compare them.
