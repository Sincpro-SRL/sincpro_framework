# Repository, tables and writes

## Tables and the repository

The table is declared once, in infrastructure. `entity_table` adds the four `Entity` columns;
`map_aggregates` ties each class to its table and infers relations from the foreign keys.

```python
from sqlalchemy import Column, ForeignKey, Integer, Text
from sqlalchemy.orm import registry
from sincpro_framework.orm import Database, Repository, archive_columns, entity_table, map_aggregates

billing = registry()
customer_table = entity_table(
    "customer", billing.metadata,
    Column("name", Text, nullable=False),
    Column("email", Text),
    *archive_columns(),                       # for ArchivableMixin
)
invoice_table = entity_table(
    "invoice", billing.metadata,
    Column("number", Text, nullable=False, unique=True),
    Column("customer_id", Text, ForeignKey("customer.id"), nullable=False),
    Column("total", Integer, nullable=False),
    Column("state", Text, nullable=False),
)
map_aggregates(billing, {Customer: customer_table, Invoice: invoice_table})

database = Database("postgresql://…")          # any SQLAlchemy URL; extras go to create_engine
repository = Repository(database)              # add_dependency("repository", repository)
```

`Database(url, pool_size=10, pool_pre_ping=True)` passes the rest to `create_engine`. Every
statement is logged at DEBUG, traced when OpenTelemetry is on, and reported to GlitchTip when it
fails.

## Writing

`save` inserts a new aggregate and updates a stored one — the aggregate says which by its `version`
(`0` is never stored). It takes one aggregate or a list.

```python
repository.save(luis)                       # luis.version == 1 after
repository.save([Invoice(number="F-001", …), Invoice(number="F-002", …)])

first = repository.get_by(Invoice, number="F-001")
first.state = "posted"
repository.save(first)                      # update: version 1 → 2
```

**A stale write is refused.** Two requests read the same row; the second to save loses instead of
silently overwriting.

```python
from sincpro_framework.ddd import StaleAggregate

mine = repository.get(Invoice, first.id)
theirs = repository.get(Invoice, first.id)
mine.total = 110
repository.save(mine)
theirs.total = 999
try:
    repository.save(theirs)
except StaleAggregate:
    ...                                      # refused
```

`Repository.retrying(fn, ...)` re-runs on `StaleAggregate` for writes that should just try again.

**Removing and archiving.**

```python
repository.remove(temporary)                # deletes the row
repository.archive(luis)                    # ArchivableMixin: stamps archived_at, keeps the row
```

## A unit of work

Each `save` is its own transaction. `repository.context()` gives one session and one transaction for
a block: everything commits together, or nothing if the block raises.

```python
with repository.context() as unit:
    posted = unit.get_by(Invoice, number="F-002")
    posted.state = "posted"
    unit.save(posted)
    unit.save(Invoice(number="F-004", customer_id=ana.id, total=75))
```

Inside the block, relations load lazily on first touch. Outside it, only what a `specification`
asked for comes back. This is the shape for an outbox — the state change and its fact commit
together (`sincpro-framework-domain-events`).

## Reading

The short questions:

```python
repository.exists(Invoice, Criteria(where=Condition(field="number", value="F-001")))
repository.get(Invoice, invoice_id)               # one or None
repository.get_by(Invoice, number="F-001")         # keyword lookup
repository.count(Invoice).value
repository.pluck(Invoice, "number")                # one column
repository.distinct(Invoice, "state")
repository.first(Invoice, criteria)
repository.one(Invoice, criteria)                  # exactly one, else raises
```

The rest of the surface — `search`, `fetch_all`, `stream`, `browse`, `measures`, `group_by`,
`pivot`, `export`, `explain`, `narrowed` — is `sincpro-framework-criteria`.

**Multi-tenant narrowing.** `narrowed(criteria)` hands out a repository that can only see — and
only write — what the criteria allows:

```python
anas_books = repository.narrowed(Criteria(where=Condition(field="customer_id", value=ana.id)))
anas_books.count(Invoice).value
```

## Repository is an abstract class, not a Protocol

`ddd.repositories.Repository` is abstract so structural typing cannot pass an implementation with
the wrong arguments. `MemoryRepository` implements the same vocabulary with no database (tests).
