# Repository, tables and writes

## Tables and the repository

The table is declared once, in infrastructure. `entity_table` adds the four `Entity` columns
(`id`, `created_at`, `updated_at`, `version`); `map_aggregates` ties each class to its table,
turns `version` into SQLAlchemy's `version_id_col` (the stale-write check) and infers relations
from the foreign keys. Calling it twice changes nothing.

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


def apply_mappings() -> None:
    map_aggregates(billing, {Customer: customer_table, Invoice: invoice_table})


database = Database("postgresql+psycopg://…")   # one per bounded context
repository = Repository(database)              # add_dependency("repository", repository)
```

Call `apply_mappings()` when the bus is built (from `config_<ctx>_framework`), not on import:
nothing imports `tables.py` on its own, and an unmapped class fails on its first read with
SQLAlchemy's `NoInspectionAvailable`.

A table that predates the convention maps its own column names onto the `Entity` attributes:
`map_aggregates(reg, {Dataset: dataset_table}, properties={Dataset: {"id": dataset_table.c.dataset_id}})`.

`Database(url, echo=False, logger=None, actor=None, enforce_foreign_keys=False, **engine_options)`:

- `engine_options` go straight to `create_engine` (`pool_size=10, pool_pre_ping=True`); the
  framework picks no driver and no pool.
- `actor` answers who is writing, for `AuditedMixin`:
  `Database(url, actor=lambda: bus.current_context().get("user.id"))`. Read on every flush.
- `enforce_foreign_keys=True` makes SQLite refuse orphans as Postgres does (tests).
- Every statement is logged at DEBUG (as `sincpro_framework.sql`, or to `logger`), gets a span when
  tracing is on, and every failure goes to the error tracker when a Sentry/GlitchTip DSN is set.
  Parameter values never reach a log, a span or a report.
- `database.before_flush(run)`, `after_flush`, `after_commit`, `after_rollback` take
  `run(session)` and see every write through that database. `after_commit` is the moment the
  data is final.

## Writing

`save` inserts a new aggregate and updates a stored one — the aggregate says which by its
`version` (`0` is never stored). It takes one aggregate, a list, or a page; a batch is one flush.

```python
repository.save(luis)                       # luis.version == 1 after
repository.save([Invoice(number="F-001", …), Invoice(number="F-002", …)])

first = repository.get_by(Invoice, number="F-001")
first.state = "posted"
repository.save(first)                      # UPDATE … WHERE version = 1 → version 2
```

**`save` writes the aggregate whole.** A to-many tied by a foreign key is written by its root:
its children get the root's key and are inserted or updated with it, and an **assignment over a
whole reading** settles the children that reading saw and the root no longer holds, as the
relation declares: `orphans=Orphans.DELETE` deletes them, `Orphans.DETACH` sets their key to NULL,
and **with nothing declared the write is refused** (`ContractViolation` naming the relation).
`remove(root)` follows the same declaration: a root with children under a silent relation is
refused — delete them first, or declare it. A reference (`owned=False`) is left to its foreign
key. Inside `context()` an assignment over an unread relation reads it first.

```python
with self.repository.context() as unit:
    workspace = unit.get_by(Workspace, code=dto.code)
    workspace.repositories = [CodeRepository(name=n) for n in dto.names]   # the set, now
    unit.save(workspace)                     # new ones inserted, previous ones deleted
```

A relation that was only read — whole, paged or filtered — never deletes; an assignment with no
whole reading behind it (a page, a filter, outside `context()`) saves and logs a warning. A child
another transaction added after the read is never an orphan. Move a child between roots in one
call, `save([old_root, new_root])`. `owned=False` makes a reference to another aggregate, never
written nor removed; `orphans=Orphans.DELETE`/`Orphans.DETACH` overrides the key column.

`updated_at` is stamped and `version` raised by the adapter; the caller does neither.
`save` is not a merge: an aggregate built by hand with an id that already exists is a
`DuplicateAggregate`, not an update.

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
    ...                                      # read again, decide again
```

What the engine refuses, in the layer's own words — catch these, never a driver exception:

| Raised | When | Retry? |
|---|---|---|
| `StaleAggregate` | a newer `version` in the row | yes, on a fresh read |
| `DuplicateAggregate` | a unique value or id already taken | no |
| `ConstraintViolation` | a missing referenced row, an empty value, a check | no — the write is wrong |
| `TransactionConflict` | serialization failure, deadlock | yes, on a fresh read |
| `TimedOut` | `nowait` on a held row, a statement past `timeout` | no — the bound was yours |

Suggested HTTP mapping: `InvalidCriteria` → 400, `ContractViolation`/`ConstraintViolation` → 422,
the three conflicts → 409.

`repository.retrying(work, attempts=3, wait=0.05)` re-runs a callable on `StaleAggregate` or `TransactionConflict` when it
lost a race, with a doubling wait, and re-raises the last failure. The callable must read again:

```python
def post() -> ResponsePostInvoice:
    with self.repository.context() as unit:
        invoice = unit.get(Invoice, dto.invoice_id)     # fresh read on every attempt
        invoice.post()
        unit.save(invoice)
        return ResponsePostInvoice(invoice_id=invoice.id)

return self.repository.retrying(post)
```

**Removing and archiving.**

```python
repository.remove(temporary)                # DELETE; takes records, never ids
repository.archive(luis)                    # ArchivableMixin only: stamps archived_at, keeps the row
```

`archive` on an aggregate without `ArchivableMixin` raises `ContractViolation`. Reads skip
archived rows unless the criteria names `archived_at`; `get` answers `None` for one.

## A unit of work

Outside a block each call opens its own session and commits. `repository.context()` gives one
session and one transaction for a block: everything commits together, or nothing if the block
raises. Nesting reuses the session in play; a narrowed repository stays narrowed inside.

```python
with repository.context() as unit:
    posted = unit.get_by(Invoice, number="F-002")
    posted.state = "posted"
    unit.save(posted)
    unit.save(Invoice(number="F-004", customer_id=ana.id, total=75))
```

**Configured where it begins**: `context(isolation=Isolation.SERIALIZABLE)`, `context(read_only=True)`
(every write refused), `context(timeout=5.0)` (Postgres), `context(engine={...})` (SQLAlchemy's
`execution_options`, as they come). A level the engine lacks is refused; SQLite runs every level
as `serializable`. A nested block that asks for other options is refused, and so is `retrying`
inside a block — call it around the `context()`.

**The commit writes what the block changed**, saved or not — but only `save` runs hooks and the
cascade. Always `save`.

Only inside the block:

- relations load lazily on first touch (outside, only what a `specification` named);
- `get(…, for_update=True)` / `search(…, for_update=True)` hold row locks until the block commits;
  `skip_locked=True` passes over held rows, `nowait=True` raises `TimedOut` at once —
  never both (ignored on SQLite; refused outside a block);
- `unit.after_commit(fn)` / `unit.after_rollback(fn)`: what runs once **this** transaction
  committed or was undone — where a Feature publishes. Dropped with a savepoint that rolls back;
  a raising callback is logged, the rest still run;
- `unit.session` is SQLAlchemy whole — joins, windows, raw SQL — in the same transaction;
  `unit.flush()`, `unit.commit()` (long jobs: each batch durable on its own) and
  `with unit.savepoint():` (one part fails alone).

```python
with self.repository.context(isolation=Isolation.SERIALIZABLE) as unit:
    account = unit.get(Account, dto.account_id)
    account.withdraw(dto.amount)                 # the rule lives on the aggregate
    unit.save(account)
    unit.after_commit(lambda: self.publisher.publish_all(account.pull_events()))
```

This is also the shape for an outbox — the state change and its fact commit together
(`sincpro-framework-domain-events`).

## Writes past the aggregate

Two doors skip the aggregate's machinery on purpose — use them only where there is no rule to
keep:

```python
repository.upsert(rates, on=("currency",))                       # insert or overwrite by a unique key
repository.upsert(rates, on=("currency",), update=("per_usd",))  # overwrite only these
done = repository.upsert(rates, on=("currency",))                # done.written, done.skipped
count = repository.update_all(Session, Criteria(where=expired), {"state": "closed"})
count = repository.remove_all(Session, Criteria(where=expired))
```

- `upsert`: answers `Upserted(written, skipped)`; the key must be unique in the table; no
  version check; `before_save`/`after_save` run, `before_create`/`before_update` do not; the
  records handed in are not refreshed; Postgres and SQLite only.
- `update_all`/`remove_all`: one statement, the count back; no hooks, no cascade, no change
  tracking; `version` raised and `updated_at` stamped; scope and archived apply as in a read; a
  page or a condition the aggregate cannot answer is **refused** (it would widen the write).
- A child a root owns is written by `save(root)` — never reach for these to rebuild children.

## Reading

The short questions:

```python
from sincpro_framework.ddd import Condition, Criteria

repository.get(Invoice, invoice_id)                # one or None
repository.get_by(Invoice, number="F-001")         # natural key; two matches → ContractViolation
repository.browse(Invoices, [id_3, id_1])          # by ids, in the order given, missing skipped
repository.exists(Invoice, Criteria(where=Condition(field="number", value="F-001")))
repository.count(Invoice).value                    # Count(value, exact)
repository.pluck(Invoice, "number")                # one column of the whole result set
repository.distinct(Invoice, "state")
repository.first(Invoices, criteria)               # or None
repository.one(Invoices, criteria)                 # exactly one, else ContractViolation
```

`search` answers **one page** (50 rows by default). The rest of the surface — `search`,
`fetch_all`, `stream`, `measures`, `group_by`, `pivot`, `export`, `explain`, `statement`/`run` —
is `sincpro-framework-criteria`.

**Multi-tenant narrowing.** `narrowed(criteria)` hands out a repository that can only see — and
only write — what the criteria allows; a write outside it raises `ContractViolation`, and an
aggregate that cannot answer the scope is refused rather than read wide:

```python
anas_books = repository.narrowed(Criteria(where=Condition(field="customer_id", value=ana.id)))
anas_books.count(Invoice).value
```

## The abstraction and the test double

`sincpro_framework.ddd.Repository` is an abstract class, not a `Protocol`, so an implementation
with the wrong signatures is refused. `sincpro_framework.orm.Repository` implements it and adds
`context`, `narrowed`, `retrying`, `statement`/`run`, `pivot`, `export`, `explain`.

`MemoryRepository(*records, hooks=None, actor=None)` implements the abstract surface over a dict:
version check, `updated_at`/actor stamping, archived rows left out, hooks. It has **no**
`context()`, no relations and no date grains, and refuses `for_update=True`. Hooks are keyword-only there
(`MemoryRepository(hooks=billing_hooks)`); passing them positionally is refused.

```python
from sincpro_framework.ddd import MemoryRepository
from sincpro_framework.testing import override_dependencies


def test_registering_a_customer_stores_it():
    in_memory = MemoryRepository()
    with override_dependencies(billing, repository=in_memory):
        answer = billing(CommandRegisterCustomer(name="Eva"), ResponseRegisterCustomer)
    assert in_memory.get(Customer, answer.customer_id).name == "Eva"
```

It hands back the record it holds, not a copy: a change without `save` is visible to the test
and lost in production. To exercise a stale write, hold `copy.deepcopy(...)` of two reads. What
only a database proves — a unique index, a foreign key, a lock, a missing column — needs the real
one (SQLite in tests is enough for most).
