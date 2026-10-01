---
name: sincpro-framework-persistence
description: Persist aggregates with sincpro_framework — declare an Entity/aggregate, map its table, get/save through the Repository, wrap writes in a unit of work, resolve relations, and put rules on hooks or mixins. Use whenever the task touches aggregates, tables, `Repository`, `save`/`remove`/`archive`, `repository.context()`, `entity_table`, `map_aggregates`, relations, `Hooks`, or `ArchivableMixin`/`AuditedMixin`/`ChangeTrackingMixin` in a Sincpro Python service.
---

# sincpro-framework-persistence

`sincpro_framework.ddd` is the vocabulary (aggregates, `Repository`, hooks, events) and needs
nothing installed. `sincpro_framework.orm` is the SQLAlchemy adapter:
`pip install sincpro-framework[sqlalchemy]`. A Feature reads and writes through `self.repository`
and never learns which database is behind it.

This skill stands alone. The framework repo also has a longer, test-backed walkthrough
(`docs/persistence/guide.md` in the framework repo); it is not shipped with the package.

## Context

- **Problem it solves:** one way to keep an aggregate — a plain dataclass with its rules — in a
  database, with a version check against lost updates, a unit of work for atomic writes, batched
  relations (no hidden N+1) and per-aggregate rules (hooks) that run on every write path.
- **It is not** an Active Record (the class never knows its table), not a generic CRUD layer
  (there is no update/delete by criteria), and not a query language for complex reporting —
  joins, windows and bulk statements go through `repository.session` inside `context()`.
- **Do not use it** for data that never lives as an aggregate in this service: another bounded
  context's records are reached through that context's bus (a Command, or `Relation.bus`), and an
  external system is an adapter registered as a dependency.
- Reading lists, filters, pages and totals is `sincpro-framework-criteria`; events, change
  tracking, outbox and event sourcing are `sincpro-framework-domain-events`.

## Abstractions

| Term | What it is | Kind | Import |
|---|---|---|---|
| `Entity` | Base of every aggregate: `id` (UUID v7 hex), `created_at`, `updated_at`, `version` (0 = never stored) | dataclass base | `from sincpro_framework.ddd import Entity` |
| aggregate | Your `@dataclass` subclass of `Entity`; the unit `save` takes | dataclass (yours) | your `domain/` package |
| `EntityCollection[T]` | A page of records: `items`, `count`, `cursor`, `dropped`, `meta` | dataclass (frozen) | `from sincpro_framework.ddd import EntityCollection` |
| `ValueObject(base, validate_fn, name)` | Factory for a validated primitive (`Email`, `Money`) | function | `from sincpro_framework.ddd import ValueObject` |
| `Repository` (ddd) | The abstract store a Feature can be typed against: `get`/`search`/`save`/`remove`/`archive` and the short reads | port (abstract) | `from sincpro_framework.ddd import Repository` |
| `Repository` (orm) | The SQLAlchemy implementation; adds `context()`, `narrowed()`, `retrying()`, `statement()`/`run()`, `pivot`, `export`, `explain`, `session` | adapter | `from sincpro_framework.orm import Repository` |
| `MemoryRepository` | Same vocabulary over a dict, for tests; no unit of work, no relations | adapter (in-memory) | `from sincpro_framework.ddd import MemoryRepository` |
| `Database` | One engine + session factory; stamps `updated_at`/`created_by`, logs and traces every statement | adapter | `from sincpro_framework.orm import Database` |
| `entity_table` | `Table` with the four `Entity` columns prepended | function | `from sincpro_framework.orm import entity_table` |
| `archive_columns` / `audit_columns` | The columns `ArchivableMixin` / `AuditedMixin` need | function | `from sincpro_framework.orm import archive_columns, audit_columns` |
| `map_aggregates` | Imperative mapping class → table; switches on the version check; infers relations from FKs; idempotent | function | `from sincpro_framework.orm import map_aggregates` |
| `Relation` (orm) | Declares what the tables cannot say: `many_to_many`, `id_list`, `foreign_key`, `bus`, `resolved_by` | registry (declared into `map_aggregates(relations=…)`) | `from sincpro_framework.orm import Relation` |
| `Relation` (ddd) | Same declaration without the database kinds (`bus`, `resolved_by` only) | registry | `from sincpro_framework.ddd import Relation` |
| unit of work | `with repository.context() as unit:` — one session, one transaction, lazy relations | method (orm) | — |
| `Hooks` | A bounded context's collection of hooks, handed to a repository | registry | `from sincpro_framework.ddd import Hooks` |
| `Hook` | One rule class; its methods are moments (`before_save`, `after_create`…) | base class | `from sincpro_framework.ddd import Hook` |
| `ArchivableMixin` | `archived_at`; `archive()` hides instead of deleting | dataclass mixin | `from sincpro_framework.ddd import ArchivableMixin` |
| `AuditedMixin` | `created_by`/`updated_by`, stamped from `Database(actor=…)` | dataclass mixin | `from sincpro_framework.ddd import AuditedMixin` |
| `ChangeTrackingMixin` | One `EntityUpdated` per save with every changed field | dataclass mixin | `from sincpro_framework.ddd import ChangeTrackingMixin` |
| `StaleAggregate` / `DuplicateAggregate` | A write lost a race: newer version / unique value taken | exception | `from sincpro_framework.ddd import StaleAggregate, DuplicateAggregate` |
| `ContractViolation` / `RelationNotResolved` | API used against its contract / relation read that nobody asked for | exception | `from sincpro_framework.ddd import ContractViolation, RelationNotResolved` |

"Aggregate" is the DDD root; a sum or a count is a **measure** (`sincpro-framework-criteria`).
A `Hook` is your rule for one aggregate; a repository mixin (`ChangeTrackingRepositoryMixin`) is
framework behaviour of the store — you write hooks, not repository subclasses.

## Architecture

**(a) Inside the framework.** `sincpro_framework/ddd/` holds the ports and vocabulary
(`Entity`, mixins, `Criteria`, the abstract `Repository`, `Hooks`, `MemoryRepository` as the
stdlib default). `sincpro_framework/orm/sqlalchemy/` is the only place SQLAlchemy is imported
(`Database`, the SQL `Repository`, `data_mapper.py`, `relation_resolver.py`); importing
`sincpro_framework.orm` without the `[sqlalchemy]` extra raises an `ImportError` naming the extra.

**(b) Inside a consumer service** — one `UseFramework` bus per bounded context:

```text
domains/billing/
  __init__.py                 billing = config_billing_framework("billing"); then `from . import services`
  domain/invoice.py           @dataclass Invoice(Entity) · Invoices(EntityCollection[Invoice]) — no DB import
  infrastructure/
    tables.py                 registry() · entity_table(...) · def apply_mappings(): map_aggregates(...)
    dependencies.py           DependencyContextType (repository: Repository) · register_dependencies():
                                billing_hooks.inject(bus); add_dependency("repository", Repository(database, billing_hooks))
    framework.py              typed Feature/ApplicationService/Hook bases · config_billing_framework():
                                UseFramework(...), apply_mappings(), register_dependencies()
  services/
    register_invoice.py       Command* · Response* · @billing.feature(...) class RegisterInvoice(Feature)
    hooks/__init__.py         billing_hooks = Hooks()        # walks this package on first use
    hooks/total_is_positive.py  @billing_hooks.on(Invoice) class TotalIsPositive(Hook)
```

The bus is built in `infrastructure/framework.py` and created in the context's `__init__.py`
**before** `services/` is imported, because the decorators register against that instance.
Schema changes are migrations (`sincpro-framework-operations`); `metadata.create_all` is for tests.

**(c) One call — a Feature saving an aggregate:**

```text
bus(CommandPostInvoice) → PostInvoice.execute
  with self.repository.context() as unit:      one session, one transaction
    invoice = unit.get(Invoice, id)             row → dataclass (after_read hooks)
    invoice.post()                              pure domain rule, may invoice.record(event)
    unit.save(invoice)                          before_save/before_update hooks
                                                → flush: UPDATE … WHERE version = :loaded
                                                  0 rows → StaleAggregate · unique clash → DuplicateAggregate
                                                → after_save/after_update hooks (NOT committed yet)
  block exits                                   COMMIT  (an exception inside → ROLLBACK)
  publish invoice.pull_events()                 only after the commit
```

## Mistakes an agent makes

Silent ones first — the runtime gives no error for these.

- **A dataclass field with no `Column` is never stored.** It saves without error and comes back
  with its default. Every persisted field needs a column in `entity_table(...)`.
- **A mixin without its columns does nothing.** `ArchivableMixin` without `*archive_columns()`:
  `archive()` stamps nothing durable and archived rows keep appearing (only a `Dropped("archived_at")`
  hints at it). `AuditedMixin` needs `*audit_columns()` **and** `Database(url, actor=lambda: …)`,
  otherwise `created_by`/`updated_by` stay `None`.
- **Changing an aggregate without `save` loses the change** against the database — and a test on
  `MemoryRepository` still passes, because it hands back the very object it holds. Always `save`.
- **Several `save` calls outside `context()` are separate transactions.** A failure in the second
  leaves the first committed. Writes that must succeed together go in one `with repository.context()`.
- **`after_save` is not "after commit".** Inside `context()` the block can still roll back; sending
  an email or publishing from a hook announces a fact that may never happen. Publish
  `pull_events()` after the block (or use the outbox, `sincpro-framework-domain-events`).
- **Hooks that never run.** `Hooks()` imports the package whose `__init__.py` created it; built
  in an ordinary module it imports only that module, so hooks elsewhere never register. A
  collection not passed to the repository (`Repository(database, billing_hooks)`,
  `MemoryRepository(hooks=billing_hooks)`) runs nothing.
  `assert list(billing_hooks)` in a test proves the wiring.
- **State on `self` in a hook or Feature** is shared by every request on every thread — one
  instance serves them all. Keep request data in locals.
- **A plain `Table(...)` instead of `entity_table`** without a `version` column maps without the
  stale-write check: concurrent writers overwrite each other quietly.
- **`retrying(work)` with a `work` that does not re-read** fails the same way every attempt. The
  callable opens `context()`, reads, decides and saves.
- Loud, but common: reading a relation outside `context()` that the `specification` did not name
  raises `RelationNotResolved`; `for_update=True` outside `context()` raises `ContractViolation`.

## The shape

```python
# domains/billing/domain/invoice.py
from dataclasses import dataclass, field
from sincpro_framework.ddd import Entity, EntityCollection

@dataclass
class Customer(Entity):
    name: str = ""
    invoices: list["Invoice"] = field(default_factory=list)   # one2many, read off the FK

@dataclass
class Invoice(Entity):
    number: str = ""
    customer_id: str = ""
    total: int = 0
    state: str = "draft"
    customer: Customer | None = None                          # many2one, same FK

class Invoices(EntityCollection[Invoice]): ...

# domains/billing/infrastructure/tables.py
from sqlalchemy import Column, ForeignKey, Integer, Text
from sqlalchemy.orm import registry
from sincpro_framework.orm import entity_table, map_aggregates

billing_registry = registry()
customer_table = entity_table("customer", billing_registry.metadata, Column("name", Text, nullable=False))
invoice_table = entity_table(
    "invoice", billing_registry.metadata,
    Column("number", Text, nullable=False, unique=True),
    Column("customer_id", Text, ForeignKey("customer.id"), nullable=False),
    Column("total", Integer, nullable=False),     # NOT NULL: a page can order by it
    Column("state", Text, nullable=False),
)

def apply_mappings() -> None:                     # called by config_billing_framework
    map_aggregates(billing_registry, {Customer: customer_table, Invoice: invoice_table})

# domains/billing/services/register_customer.py
@billing.feature(CommandRegisterCustomer)
class RegisterCustomer(Feature):                  # the context's typed base: self.repository
    def execute(self, dto: CommandRegisterCustomer) -> ResponseRegisterCustomer:
        customer = Customer(name=dto.name)
        self.repository.save(customer)
        return ResponseRegisterCustomer(customer_id=customer.id)
```

Type `repository:` on `DependencyContextType` with `sincpro_framework.orm.Repository` when
Features use `context()`, `narrowed()` or `retrying()`; with the `ddd` abstraction when they only
use the common surface (then `MemoryRepository` substitutes in tests).

## The rules that matter

- **An aggregate is a plain `@dataclass` inheriting `Entity`** (mixins first:
  `class Customer(ArchivableMixin, Entity)`). It imports nothing from the database. `id` is a
  UUID v7, so ordering by `id` is ordering by creation.
- **One table per aggregate, declared once in infrastructure** with `entity_table`.
  `map_aggregates` infers relations from foreign keys; declare only what the tables cannot say.
- **Writes are explicit, reads are generic.** `save` one or many, `remove`, `archive`. No
  update/delete by criteria. A hand-built aggregate with an existing id is a duplicate, not an
  update: read it, change it, save it.
- **A stale write is refused** (`StaleAggregate`); read again and decide again.
- **`repository.context()` is one unit of work**: everything commits together or not at all.
- **A relation is resolved once per page, and refused if you did not ask for it.** No hidden N+1.
- **The aggregate records events; the Feature publishes them** after the write commits.

## References

- [references/aggregate.md](references/aggregate.md) — `Entity`, mixins, collections, value objects, extension
- [references/repository-and-writes.md](references/repository-and-writes.md) — tables, `save`/`remove`/`archive`, unit of work, reads, testing
- [references/relations.md](references/relations.md) — the kinds, inference, cost
- [references/hooks-and-mixins.md](references/hooks-and-mixins.md) — `Hook`/`Hooks`, moments, wiring, ordering

## Related

- Queries, listings, counts, measures: `sincpro-framework-criteria`
- Domain events, change tracking, event sourcing, outbox: `sincpro-framework-domain-events`
- Migrations of these tables: `sincpro-framework-operations`
- Bus, Features, `DependencyContextType`: `sincpro-framework` (router) and `sincpro-framework-core`
