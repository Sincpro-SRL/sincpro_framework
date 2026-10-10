---
name: sincpro-framework-persistence
description: Persist aggregates with sincpro_framework — Entity, IRepository capabilities, AggregateRepository, shared unit of work, Writes.SAVED/CHANGED, transactional numbering, relations, hooks, and the built-in reads of one aggregate (presentation, EntityReads with Get/GetMany/LiteralSearch/Search/DomainEvents). Use when a task touches tables, get/save/remove/archive, repository.context(), entity_table/map_aggregates, DatabaseNumbering, aggregate mixins, or a Feature that reads one entity (get by id, get many, a select by text, a list, its event history) in a Sincpro Python service.
---

# sincpro-framework-persistence

`sincpro_framework.ddd` is the vocabulary (aggregates, `IRepository`, hooks, events) and needs
nothing installed. `sincpro_framework.data_layer.orm` is the SQLAlchemy adapter:
`pip install sincpro-framework[sqlalchemy]`. A Feature reads and writes through `self.repository`
and never learns which database is behind it.

This skill stands alone. The framework repo also has a longer, test-backed walkthrough
(`docs/persistence/guide.md` in the framework repo); it is not shipped with the package.

## Context

- **Problem it solves:** one way to keep an aggregate — a plain dataclass with its rules — in a
  database, with a version check against lost updates, a unit of work for atomic writes, batched
  relations (no hidden N+1) and per-aggregate rules (hooks) that run on every write path.
- **It is not** an Active Record (the class never knows its table), not a generic CRUD layer
  (writes take the aggregate; the bulk doors `update_all`/`remove_all` are named for what they
  skip), and not a query language for complex reporting — joins and windows go through
  `repository.session` inside `context()`.
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
| `IRepository` | Aggregate reads and writes, with hooks; optional capabilities are separate | port | `from sincpro_framework.ddd import IRepository` |
| `ReadsAggregates` / `WritesAggregates` / `Analyzes` / `WritesInBulk` / `Transacts` | Declare the operations a consumer actually requires; `StoreCapabilities` describes backend support | capability ports | `sincpro_framework.ddd` |
| `AggregateRepository[T]` / `DatabaseAggregateRepository[T]` | Typed view bound to one aggregate; the database view additionally exposes SQL capabilities | adapter views | `sincpro_framework.ddd` / `sincpro_framework.data_layer.orm` |
| `Repository` (orm) | The SQLAlchemy implementation; adds `context()`, `narrowed()`, `retrying()`, `statement()`/`run()`, `pivot`, `export`, `explain`, `session` | adapter | `from sincpro_framework.data_layer.orm import Repository` |
| `MemoryRepository` | Same vocabulary over a dict, for tests; no unit of work, no relations | adapter (in-memory) | `from sincpro_framework.data_layer.repositories import MemoryRepository` |
| `Database` | One engine + session factory; stamps `updated_at`/`created_by`, logs and traces every statement | adapter | `from sincpro_framework.data_layer.orm import Database` |
| `entity_table` | `Table` with the four `Entity` columns prepended | function | `from sincpro_framework.data_layer.orm import template_table` → `template_table.entity_table` |
| `archive_columns` / `audit_columns` | The columns `ArchivableMixin` / `AuditedMixin` need | function | `from sincpro_framework.data_layer.orm import template_table` → `template_table.archive_columns` / `template_table.audit_columns` |
| `map_aggregates` | Imperative mapping class → table; switches on the version check; infers relations from FKs; idempotent | function | `from sincpro_framework.data_layer.orm import map_aggregates` |
| `Relation` (orm) | Declares what the tables cannot say: `many_to_many`, `id_list`, `foreign_key`, `bus`, `resolved_by` | registry (declared into `map_aggregates(relations=…)`) | `from sincpro_framework.data_layer.orm import Relation` |
| `Relation` (ddd) | Same declaration without the database kinds (`bus`, `resolved_by` only) | registry | `from sincpro_framework.ddd import Relation` |
| unit of work | `with repository.context() as unit:` — one session, one transaction, lazy relations | method (orm) | — |
| `Writes.SAVED` / `Writes.CHANGED` | Default explicit saves / opt-in session-tracked writes at commit | enum | `sincpro_framework.data_layer.orm` |
| `INumbering` / `MemoryNumbering` | `next_number(series, scope="")`, `take(series, count=1, scope="") -> range` | port / test double | `sincpro_framework.ddd` |
| `DatabaseNumbering` / `numbering_table` | Number allocation participating in the database unit of work | adapter / table factory | `sincpro_framework.data_layer.orm` |
| `Hooks` | A bounded context's collection of hooks, handed to a repository | registry | `from sincpro_framework.ddd import Hooks` |
| `Hook` | One rule class; its methods are moments (`before_save`, `after_create`…) | base class | `from sincpro_framework.ddd import Hook` |
| `ArchivableMixin` | `archived_at`; `archive()` hides instead of deleting | dataclass mixin | `from sincpro_framework.ddd import ArchivableMixin` |
| `AuditedMixin` | `created_by` on insert, `updated_by` on each later update (it stays `None` until the first update), from `Database(actor=…)` | dataclass mixin | `from sincpro_framework.ddd import AuditedMixin` |
| `ChangeTrackingMixin` | One `EntityUpdated` per save of a stored aggregate, with every changed field; an insert records nothing — record your own created event | dataclass mixin | `from sincpro_framework.ddd import ChangeTrackingMixin` |
| `StaleAggregate` / `DuplicateAggregate` | A write lost a race: newer version / unique value taken | exception | `from sincpro_framework.ddd import StaleAggregate, DuplicateAggregate` |
| `ProgrammingError` / `RelationNotResolved` | API used against its contract / relation read that nobody asked for | exception | `from sincpro_framework.ddd import ProgrammingError, RelationNotResolved` |
| `DEFAULT_GET_ID` / `DEFAULT_READING` / `DEFAULT_ORDER` / `DEFAULT_DISPLAY` / `DEFAULT_LITERAL_SEARCH` | How the entity is read: the key `Get` finds it by, what one record brings (`Specification`), the list order (`Sort`s), the field shown beside the identity, the template a typed text fills (`Criteria` with `TEXT`). `@classmethod`s `Entity` defines with defaults; override what you need; what a caller's criteria names wins | `@classmethod` on the entity | `Entity`; `TEXT` from `sincpro_framework.ddd` |
| `Presentation` | Class attribute of an entity: form hints only (`readonly`, `required`, `readonly_when`, `required_when`, `visible_when`), fields named by lambda (`lambda a: a.code`) | declaration | `from sincpro_framework.ddd import Presentation` |
| `Is` / `When` / `AllHold` / `AnyHolds` | A hint's condition as the Criteria triple: `When(Is(i.state, Operator.NE, State.DRAFT), i.partner_id)` | declaration | `sincpro_framework.ddd` |
| `Get` / `GetMany` / `LiteralSearch` / `Search` / `DomainEvents` | Generic DTO bases naming entity and response: `class QueryGetInvoice(Get[Invoice, ResponseInvoice])`; `DomainEvents` answers the record's events from the event class its response holds | DTO bases | `sincpro_framework.ddd` |
| `history_of(*records)` | The criteria that reads several records' events together, each by its type and identity | function | `sincpro_framework.ddd` |
| `EntityReads[T]` | The Feature that answers those four DTOs from the entity's `DEFAULT_*`; the caller's criteria wins part by part (`Criteria.replaced_by`); extend by overriding `get`/`get_many`/`literal_search`/`search` | Feature base | `from sincpro_framework.ddd import EntityReads` |
| `ResponseRecord` / `ResponseRecords` | One record / a list by key (with `missing`), cut by the specification on the wire | response bases | `sincpro_framework.ddd` |
| `AggregateNotFound` | `Get` named a key with no record; `not_found` on every wire (404 REST) | exception | `from sincpro_framework.ddd import AggregateNotFound` |
| `Derivations` / `Derive` | Class attribute: fields the entity computes from others, by its own methods, run in dependency order by `recompute` and by `save` (children first, `recompute_whole`); `upsert`/`update_all` compute nothing | declaration | `sincpro_framework.ddd` |
| `assign` / `recompute` | Put a form's values on a record (read as each field's type) / run the derivations a change reaches | functions | `sincpro_framework.ddd` |
| `Preview` / `ResponsePreview` / `FieldState` | DTO base answered by `EntityReads.preview`: values that moved, field states, advice — nothing stored | DTO bases | `sincpro_framework.ddd` |
| `Drafts` / `InMemoryDrafts` / `KeyValueDrafts` / `refuse_stale` | What a form keeps between requests: optimistic version (`DraftConflict`, 409), a TTL; `refuse_stale` before activating over the stored record | port / adapters / function | `sincpro_framework.ddd` |
| `previewing` / `is_previewing` / `advise` / `ProgrammingError` | The block where every store write, numbering `take` and SQL session flush raises; advice collected inside it | context manager / function / exception | `sincpro_framework.ddd` |

"Aggregate" is the DDD root; a sum or a count is a **measure** (`sincpro-framework-criteria`).
A `Hook` is your rule for one aggregate; a repository mixin (`ChangeTrackingRepositoryMixin`) is
framework behaviour of the store — you write hooks, not repository subclasses.

## Architecture

**(a) Inside the framework.** `sincpro_framework/ddd/` holds the ports and vocabulary
(`Entity`, mixins, `Criteria`, `IRepository`, capability ports, `Hooks`, `MemoryRepository` as the
stdlib default). `sincpro_framework/data_layer/orm/sqlalchemy/` is the only place SQLAlchemy is imported
(`Database`, the SQL `Repository`, `data_mapper.py`, `relation_resolver.py`); importing
`sincpro_framework.data_layer.orm` without the `[sqlalchemy]` extra raises an `ImportError` naming the extra.

**(b) Inside a consumer service** — one `UseFramework` bus per bounded context:

```text
domains/billing/
  __init__.py                 billing = config_billing_framework("billing"); then `from . import services`
  domain/invoice.py           @dataclass Invoice(Entity) · Invoices(EntityCollection[Invoice]) — no DB import
  infrastructure/
    tables.py                 registry() · template_table.entity_table(...) · def apply_mappings(): map_aggregates(...)
    hooks.py                  billing_hooks = Hooks("<pkg>.billing.services.hooks")   # names the package it walks
    dependencies.py           DependencyContextType (repository: Repository) · register_dependencies():
                                from .hooks import billing_hooks
                                billing_hooks.inject(bus); add_dependency("repository", Repository(database, billing_hooks))
    framework.py              typed Feature/ApplicationService/Hook bases · config_billing_framework():
                                UseFramework(...), apply_mappings(), register_dependencies()
  services/
    register_invoice.py       Command* · Response* · @billing.feature(...) class RegisterInvoice(Feature)
    hooks/__init__.py         (empty)
    hooks/total_is_positive.py  from <pkg>.billing.infrastructure.hooks import billing_hooks
                                @billing_hooks.on(Invoice) class TotalIsPositive(Hook)
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
  with its default. Every persisted field needs a column in `template_table.entity_table(...)`.
- **A mixin without its columns does nothing.** `ArchivableMixin` without `*archive_columns()`:
  `archive()` stamps nothing durable and archived rows keep appearing (only a `Dropped("archived_at")`
  hints at it). `AuditedMixin` needs `*audit_columns()` **and** `Database(url, actor=lambda: …)`,
  otherwise `created_by`/`updated_by` stay `None`.
- **Changing an aggregate without `save`.** It is never written: outside `context()` the change
  is lost, inside it the change is put back at the end of the block and a warning names it
  ("Account changed inside context() and was never saved"). A test on `MemoryRepository` passes
  either way, because it hands back the very object it holds. Always `save`;
  `context(writes=Writes.CHANGED)` exists for a block that wants the session to write what it
  tracked.
- **Removing a child by mutating the collection.** A root saves its children by itself, but only
  an **assignment inside `context()`** removes: `workspace.repositories = [kept, added]` settles
  what the relation read and no longer holds. Reading and saving never deletes; outside
  `context()` an assignment cannot know what it replaced and removes nothing. `remove(root)`
  settles its children as the relation declares — `orphans=Orphans.DELETE` or
  `Orphans.DETACH`; with nothing declared it is refused while there are children. `owned=False`
  declares a reference to another aggregate (better held by id). Hand-written "remove the
  previous children, set their FK, save them" loops are not needed —
  `references/repository-and-writes.md`.
- **Several `save` calls outside `context()` are separate transactions.** A failure in the second
  leaves the first committed. Writes that must succeed together go in one `with repository.context()`.
- **`after_save` is not "after commit".** Inside `context()` the block can still roll back; sending
  an email or publishing from a hook announces a fact that may never happen. Publish
  `pull_events()` after the block (or use the outbox, `sincpro-framework-domain-events`).
- **`infrastructure/` importing from `services/`.** The hook collection lives in
  `infrastructure/hooks.py`, never in `services/hooks/__init__.py`: `services/__init__.py` imports
  the use cases, they import the bus still being built, and boot fails with a circular
  `ImportError`.
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
- **A read record that cannot be printed or compared.** Outside `context()`, `repr()`, `==`,
  `f"{record}"` and `asdict()` read every field, relations included, and an unread relation raises
  `RelationNotResolved`. Declare relation fields `field(default=None, repr=False, compare=False)` /
  `field(default_factory=list, repr=False, compare=False)`; never `asdict` an aggregate — build the
  Response field by field.
- **Redeclaring a reserved field.** `id`, `created_at`, `updated_at` and `version` (and a mixin's
  own fields: `archived_at`, `created_by`, `updated_by`) belong to `Entity`; a subclass that
  redeclares one is refused when the class is defined — `version` is the optimistic lock.
- Loud, but common: reading a relation outside `context()` that the `specification` did not name
  raises `RelationNotResolved`; `for_update=True` outside `context()` raises `ProgrammingError`.

## The shape

```python
# domains/billing/domain/customer.py  — one aggregate per file
from dataclasses import dataclass, field
from sincpro_framework.ddd import Entity

@dataclass
class Customer(Entity):
    name: str = ""
    invoices: list["Invoice"] = field(default_factory=list, repr=False, compare=False)  # one2many

from .invoice import Invoice  # noqa: E402 — at the bottom, never under TYPE_CHECKING

# domains/billing/domain/invoice.py
from dataclasses import dataclass, field
from sincpro_framework.ddd import Entity, EntityCollection
from .customer import Customer

@dataclass
class Invoice(Entity):
    number: str = ""
    customer_id: str = ""
    total: int = 0
    state: str = "draft"
    customer: Customer | None = field(default=None, repr=False, compare=False)  # many2one, same FK

    def post(self) -> None:                       # the rule lives on the aggregate it changes
        if self.total <= 0:
            raise ValueError("an empty invoice cannot be posted")
        self.state = "posted"

class Invoices(EntityCollection[Invoice]): ...

# domains/billing/infrastructure/tables.py
from sqlalchemy import Column, ForeignKey, Integer, Text
from sqlalchemy.orm import registry
from sincpro_framework.data_layer.orm import map_aggregates, template_table

billing_registry = registry()
customer_table = template_table.entity_table("customer", billing_registry.metadata, Column("name", Text, nullable=False))
invoice_table = template_table.entity_table(
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

Type `repository:` on `DependencyContextType` with `sincpro_framework.data_layer.orm.Repository` when
Features use `context()`, `narrowed()` or `retrying()`; with `ddd.IRepository` when they only
use the common surface (then `MemoryRepository` substitutes in tests).

## The rules that matter

- **An aggregate is a plain `@dataclass` inheriting `Entity`** (mixins first:
  `class Customer(ArchivableMixin, Entity)`). It imports nothing from the database. `id` is a
  UUID v7, so ordering by `id` is ordering by creation.
- **One table per aggregate, declared once in infrastructure** with `entity_table`.
  `map_aggregates` infers relations from foreign keys; declare only what the tables cannot say.
- **Writes are explicit, reads are generic.** `save` one or many — the root with the children
  it owns — `remove`, `archive`. A hand-built aggregate with an existing id is a duplicate, not
  an update: read it, change it, save it. `upsert` and `update_all`/`remove_all` exist for the
  writes with no rule to keep, and skip the hooks.
- **The transaction is configured where it begins**: `context(isolation=Isolation.SERIALIZABLE)`,
  `read_only=True`, `timeout=`; publish in `unit.after_commit(...)`, never in a hook.
- **A stale write is refused** (`StaleAggregate`); read again and decide again.
- **`repository.context()` is one unit of work** shared by repositories using the same
  `Database` instance, including child Features. Two Database objects or two stores are not
  one transaction. `separate=True` deliberately opens another transaction.
- **A relation is resolved once per page, and refused if you did not ask for it.** No hidden N+1.
- **`get(id)` is the stored aggregate**: every scalar, no relation, never a page.
  `get(id, detail=detail_of(Model))` is still that one aggregate, with the relations the
  entity's `DEFAULT_READING` names resolved onto it on SQL; `MemoryRepository` already
  holds them.
- **Reads of one aggregate are `EntityReads`, not hand-copied Features.** Name the DTOs
  (`Get`, `GetMany`, `LiteralSearch`, `Search` with `[Entity, Response]`), register them on one
  `EntityReads[Entity]`, override a read and call `super()` to extend it. What they bring by
  default is the entity's own `DEFAULT_*` class methods — never a `default_factory` on each
  DTO — and a caller's criteria wins for every part it names. Writes stay Commands with an
  intent, written by hand. Recipe: [references/entity-reads.md](references/entity-reads.md).
- **A value a form must see before saving is a derivation, not a hook.** Declare
  `derivations` on the entity; `save` and `EntityReads.preview` run the same `recompute`, and
  nothing is written inside a preview (`ProgrammingError`). Recipe:
  [references/preview.md](references/preview.md). What a form keeps between requests is a draft
  (`KeyValueDrafts` over Redis), never a half-saved record: [references/drafts.md](references/drafts.md).
- **Form hints are defaults for a client, never rules.** `presentation`'s `readonly`,
  `required`, `*_when` and the dataclass defaults reach `Meta.fields`; nothing checks them on
  save. A rule the server keeps is the project's own `before_save` hook, reading the same
  condition from `presentation_of(Entity)`. Recipe: [references/form-hints.md](references/form-hints.md).
- **The aggregate records events; save keeps mapped facts with it.** Use an `EventRelay`
  for durable delivery; manual publication after commit is not a durable outbox.

## Numbering and aggregate views

Use `AggregateRepository[Invoice](repository, Invoice)` when callers should omit the model
argument, or `DatabaseAggregateRepository[Invoice]` when they also need SQL operations.
These wrap the existing store; they are not independent connections or transactions.

`DatabaseNumbering(database, numbering_table(...))` allocates by series and optional scope.
Allocate the number **inside the same database unit of work that saves the document** so a
rollback also returns the allocation. Calling outside commits the allocation independently;
`MemoryNumbering` cannot prove concurrency or rollback. Do not use `MAX(number) + 1`.

## References

- [references/aggregate.md](references/aggregate.md) — `Entity`, mixins, collections, value objects, extension
- [references/repository-and-writes.md](references/repository-and-writes.md) — tables, `save`/`remove`/`archive`, unit of work, reads, testing
- [references/relations.md](references/relations.md) — the kinds, inference, cost
- [references/hooks-and-mixins.md](references/hooks-and-mixins.md) — `Hook`/`Hooks`, moments, wiring, ordering
- [references/entity-reads.md](references/entity-reads.md) — `DEFAULT_*` + `EntityReads`: get, get many, select by text, list
- [references/preview.md](references/preview.md) — derived fields, `assign`, `Preview`: what a record becomes before it is saved
- [references/drafts.md](references/drafts.md) — drafts between requests in memory or Redis, conflicts, activating with `refuse_stale`
- [references/form-hints.md](references/form-hints.md) — form hints in `presentation`, what a client receives, enforcing one with a hook

## Related

- Queries, listings, counts, measures: `sincpro-framework-criteria`
- Domain events, change tracking, event sourcing, outbox: `sincpro-framework-domain-events`
- Migrations of these tables: `sincpro-framework-operations`
- Bus, Features, `DependencyContextType`: `sincpro-framework` (router) and `sincpro-framework-core`
