# Persistence guide: every use case, step by step

One small billing context, built from nothing: customers, invoices and their lines. Every
block on this page runs, in order, as one program — `tests/docs/test_persistence_guide.py`
executes the page, so an example that stops working fails the build.

The other pages explain *why* each piece is the way it is. This one is *how*.

| You want to… | Section |
|---|---|
| Declare an aggregate | [1. The aggregate](#1-the-aggregate) |
| Give it a table and a database | [2. Tables and the repository](#2-tables-and-the-repository) |
| Use it from a Feature | [3. Inside a Feature](#3-inside-a-feature) |
| Create, update, delete, archive | [4. Writing](#4-writing) |
| Several writes in one transaction | [5. A unit of work](#5-a-unit-of-work) |
| Find, filter, order, page, count | [6. Reading](#6-reading) |
| Bring related records | [7. Relations](#7-relations) |
| Validate or compute on every write | [8. Hooks](#8-hooks) |
| Say what happened, and react to it | [9. Domain events](#9-domain-events) |
| Record every field that changed | [10. Change tracking](#10-change-tracking) |
| Keep the facts as the state | [11. Event sourcing](#11-event-sourcing) |
| Deliver events reliably to another process | [12. An outbox](#12-an-outbox) |
| Test a Feature without a database | [13. Testing](#13-testing) |
| Extend an aggregate with fields of its own | [14. Extending an aggregate](#14-extending-an-aggregate) |

Install the adapter with `pip install sincpro-framework[sqlalchemy]`. The vocabulary —
`sincpro_framework.ddd` — needs nothing installed; `sincpro_framework.orm` is the SQLAlchemy
adapter.

---

## 1. The aggregate

An aggregate is a plain `@dataclass` that inherits `Entity`. `Entity` gives it `id` (a UUID v7,
so ordering by id is ordering by time), `created_at`, `updated_at` and `version`. It imports
nothing from the database. `updated_at` is when it was last written — `created_at` on the insert —
so "untouched since" is one column.

```python
from dataclasses import dataclass, field

from sincpro_framework.ddd import ArchivableMixin, Entity, EntityCollection


@dataclass
class Customer(ArchivableMixin, Entity):
    name: str = ""
    email: str = ""
    invoices: list["Invoice"] = field(default_factory=list)   # one2many, read off the FK


@dataclass
class Invoice(Entity):
    number: str = ""
    customer_id: str = ""
    total: int = 0
    state: str = "draft"
    customer: Customer | None = None                           # many2one, same FK


class Customers(EntityCollection[Customer]): ...


class Invoices(EntityCollection[Invoice]): ...
```

- **Relations are annotations.** `list["Invoice"]` is a one2many, `Customer | None` a
  many2one; which column links them is read from the table's foreign key in the next step.
- **`EntityCollection[...]`** is the typed page a search answers: `items`, `count`, `cursor`,
  `dropped`, `meta`. Declaring one per aggregate is optional — `search(Invoice, ...)` works
  too — but it types the answer.
- **Mixins add a capability and its columns.** `ArchivableMixin` adds `archived_at`, so
  `archive()` hides a record instead of deleting it. `AuditedMixin` adds `created_by` /
  `updated_by`; `ChangeTrackingMixin` records what changed ([§10](#10-change-tracking)).

## 2. Tables and the repository

The table is declared once, in infrastructure. `entity_table` adds the four `Entity` columns;
`map_aggregates` ties each class to its table and infers the relations from the foreign keys.

```python
from sqlalchemy import Column, ForeignKey, Integer, Text
from sqlalchemy.orm import registry

from sincpro_framework.orm import (
    Database,
    Repository,
    archive_columns,
    entity_table,
    map_aggregates,
)

billing = registry()
customer_table = entity_table(
    "customer",
    billing.metadata,
    Column("name", Text, nullable=False),
    Column("email", Text),
    *archive_columns(),
)
invoice_table = entity_table(
    "invoice",
    billing.metadata,
    Column("number", Text, nullable=False, unique=True),
    Column("customer_id", Text, ForeignKey("customer.id"), nullable=False),
    Column("total", Integer, nullable=False),
    Column("state", Text, nullable=False),
)
map_aggregates(billing, {Customer: customer_table, Invoice: invoice_table})

database = Database("sqlite:///billing.sqlite3")
billing.metadata.create_all(database.engine)      # a real project runs its migrations instead
repository = Repository(database)
```

`Database` takes any SQLAlchemy URL and passes the rest to `create_engine`
(`Database(url, pool_size=10, pool_pre_ping=True)`). Every statement is logged at DEBUG,
traced when OpenTelemetry is on, and reported to GlitchTip when it fails.

## 3. Inside a Feature

The repository is a dependency like any other. A Feature reads it as `self.repository` and
never learns which database is behind it.

```python
from sincpro_framework import DataTransferObject, Feature, UseFramework

billing_bus = UseFramework("billing", log_after_execution=False)
billing_bus.add_dependency("repository", repository)


class CommandRegisterCustomer(DataTransferObject):
    name: str
    email: str


class ResponseRegisterCustomer(DataTransferObject):
    customer_id: str


@billing_bus.feature(CommandRegisterCustomer)
class RegisterCustomer(Feature):
    repository: Repository

    def execute(self, dto: CommandRegisterCustomer) -> ResponseRegisterCustomer:
        customer = Customer(name=dto.name, email=dto.email)
        self.repository.save(customer)
        return ResponseRegisterCustomer(customer_id=customer.id)


registered = billing_bus(
    CommandRegisterCustomer(name="Ana", email="ana@acme.bo"), ResponseRegisterCustomer
)
ana = repository.get(Customer, registered.customer_id)
assert ana.name == "Ana"
```

## 4. Writing

`save` inserts a new aggregate and updates a stored one — the aggregate says which by its
`version` (`0` is never stored). It takes one aggregate or a list.

```python
luis = Customer(name="Luis", email="luis@acme.bo")
repository.save(luis)
assert luis.version == 1

repository.save([
    Invoice(number="F-001", customer_id=ana.id, total=100),
    Invoice(number="F-002", customer_id=ana.id, total=250),
    Invoice(number="F-003", customer_id=luis.id, total=40),
])

first = repository.get_by(Invoice, number="F-001")
first.state = "posted"
repository.save(first)                     # an update: version 1 → 2
assert repository.get(Invoice, first.id).state == "posted"
```

**A stale write is refused.** Two requests read the same invoice; the second to save loses,
instead of silently overwriting the first:

```python
from sincpro_framework.ddd import StaleAggregate

mine = repository.get(Invoice, first.id)
theirs = repository.get(Invoice, first.id)
mine.total = 110
repository.save(mine)

theirs.total = 999
try:
    repository.save(theirs)
    raise AssertionError("the stale write should have been refused")
except StaleAggregate:
    pass
```

`Repository.retrying(...)` re-runs a function when that happens, for the writes that should
simply try again on fresh data.

**Deleting and archiving.** `remove` deletes the row. `archive` — for an `ArchivableMixin`
aggregate — stamps `archived_at` and keeps it, and every read skips it unless asked:

```python
from sincpro_framework.ddd import Condition, Criteria, Operator

temporary = Customer(name="Temporal")
repository.save(temporary)
repository.remove(temporary)
assert repository.get(Customer, temporary.id) is None

repository.archive(luis)
assert luis.id not in [customer.id for customer in repository.search(Customers).items]

archived = Criteria(
    where=Condition(field="archived_at", operator=Operator.IS_NULL, value=False)
)
assert [customer.name for customer in repository.search(Customers, archived).items] == ["Luis"]
```

## 5. A unit of work

Each `save` is its own transaction. `context()` gives one session and one transaction for a
block: everything commits together at the end, or nothing does if the block raises.

```python
with repository.context() as unit:
    posted = unit.get_by(Invoice, number="F-002")
    posted.state = "posted"
    unit.save(posted)
    unit.save(Invoice(number="F-004", customer_id=ana.id, total=75))

try:
    with repository.context() as unit:
        unit.save(Invoice(number="F-005", customer_id=ana.id, total=10))
        raise RuntimeError("the payment gateway is down")
except RuntimeError:
    pass
assert repository.get_by(Invoice, number="F-005") is None     # rolled back
```

Inside the block, relations load lazily on first touch; outside it, only what a
`specification` asked for comes back ([§7](#7-relations)).

## 6. Reading

The short questions:

```python
assert repository.exists(Invoice, Criteria(where=Condition(field="number", value="F-001")))
assert repository.get_by(Invoice, number="F-404") is None
assert repository.count(Invoice).value == 4
assert sorted(repository.pluck(Invoice, "number")) == ["F-001", "F-002", "F-003", "F-004"]
assert sorted(repository.distinct(Invoice, "state")) == ["draft", "posted"]
```

**`Criteria`** is the one way to ask: `where`, `order`, `pagination`, `specification`. It is a
DTO, so it can arrive as JSON from a screen or another service and be validated:

```python
big_ones = Criteria.model_validate({
    "where": {"field": "total", "operator": ">=", "value": 75},
    "order": [{"field": "total", "descending": True}],
    "pagination": {"limit": 2},
})
page = repository.search(Invoices, big_ones)

assert [invoice.number for invoice in page.items] == ["F-002", "F-001"]
assert page.count.value == 3
assert page.cursor is not None                      # there is a next page
```

A URL-style ordering (`"-total,number"`) becomes that list with
`sincpro_framework.ddd.criteria.parse_order`.

The next page is the same criteria resumed from the cursor the page handed back — in JSON,
`"pagination": {"limit": 2, "strategy": {"token": "<cursor>"}}`. A cursor names a row, not a
position, so a row inserted meanwhile shifts nothing; `Offset(rows=...)` counts rows instead.

```python
from sincpro_framework.ddd import Cursor, Pagination

next_page = repository.search(
    Invoices,
    big_ones.model_copy(
        update={"pagination": Pagination(limit=2, strategy=Cursor(token=page.cursor))}
    ),
)
assert [invoice.number for invoice in next_page.items] == ["F-004"]
```

Conditions combine with `all` / `any` / `negate`, and the operators are `Operator`: `=` (the
default), `!=`, `>`, `>=`, `<`, `<=`, `in`, `not in`, `like`, `contains`, `not contains`,
`between` and `is null`:

```python
anas_drafts = Criteria.model_validate({
    "where": {"all": [
        {"field": "customer_id", "value": ana.id},
        {"field": "state", "value": "draft"},
    ]},
})
assert [invoice.number for invoice in repository.search(Invoices, anas_drafts).items] == [
    "F-004"
]
```

A condition the model cannot answer — a field that does not exist, an operator a field does
not take — is not an error: it is left out and listed in `page.dropped`, so a screen learns what
it may ask. `page.meta` describes every field and relation the aggregate has.

**Whole sets, sums and groups:**

```python
every_invoice = repository.fetch_all(Invoices)      # every page, as one collection
assert len(every_invoice.items) == 4

assert repository.measures(Invoice, None, total=("sum", "total")) == {"total": 475}
```

**Seeing only part of the store.** `narrowed(criteria)` hands out a repository that can only
see — and only write — what the criteria allows. The usual case is a tenant:

```python
anas_books = repository.narrowed(Criteria(where=Condition(field="customer_id", value=ana.id)))
assert anas_books.count(Invoice).value == 3
```

`first`, `one`, `browse(ids)`, `stream`, `group_by`, `pivot`, `export` and `explain` are the
rest of the surface — see [reference.md](reference.md).

## 7. Relations

A `specification` says what to bring back of each record, related records included, each
with its own filter, order and page. Every relation is resolved once for the whole page, never
once per row.

```python
with_customer = Criteria.model_validate({
    "order": [{"field": "number"}],
    "specification": {"number": {}, "customer": {"specification": {"name": {}}}},
})
invoices = repository.search(Invoices, with_customer).items
assert invoices[0].customer.name == "Ana"

customers_with_invoices = Criteria.model_validate({
    "where": {"field": "name", "value": "Ana"},
    "specification": {
        "invoices": {"order": [{"field": "number"}], "pagination": {"limit": 2}},
    },
})
[customer] = repository.search(Customers, customers_with_invoices).items
assert [invoice.number for invoice in customer.invoices] == ["F-001", "F-002"]
```

A many-to-many, a relation that lives in another bounded context (answered by its bus), or one
answered by any function are declared once beside the tables with `Relation` — see
[relations.md](relations.md).

## 8. Hooks

A hook runs **inside the write**, on one aggregate: it validates, computes or refuses. It has
what a Feature of its bus has — other repositories, `self.bus`, `self.context`; only a write
back through the repository that fired it is refused, rather than left to recurse.

A hook is a class, registered for its aggregate with `@hooks.on(...)`, the way a Feature is
for its Command. Its moments are the methods it implements; it reads the bus's dependencies as
attributes, like a Feature, and `self.context` is the request in play:

```python
from sincpro_framework.ddd import ContractViolation, Hook, Hooks


class Numbering:
    def __init__(self) -> None:
        self.last = 100

    def next(self) -> str:
        self.last += 1
        return f"F-{self.last}"


invoicing_hooks = Hooks(None)


@invoicing_hooks.on(Invoice)
class TotalIsPositive(Hook):
    def before_save(self, invoice: Invoice) -> None:
        if invoice.total <= 0:
            raise ContractViolation(f"invoice {invoice.number} has no total")


@invoicing_hooks.on(Invoice, after=(TotalIsPositive,))
class NumberNewInvoices(Hook):
    numbering: Numbering

    def before_create(self, invoice: Invoice) -> None:
        if not invoice.number:
            invoice.number = self.numbering.next()


accounting = UseFramework("accounting", log_after_execution=False)
accounting.add_dependency("numbering", Numbering())
invoicing_hooks.inject(accounting)
numbered = Repository(database, invoicing_hooks)

fresh = Invoice(customer_id=ana.id, total=30)
numbered.save(fresh)
assert fresh.number == "F-101"
try:
    numbered.save(Invoice(number="F-006", customer_id=ana.id, total=0))
    raise AssertionError("the hook should have refused it")
except ContractViolation:
    pass
```

In a project the collection lives in a package — `billing_hooks = Hooks()` in
`services/hooks/__init__.py`, one hook per module beside it — and `Hooks()` imports every
module of that package the first time it is read, so no hook is forgotten. `Hooks(None)`, as
here, is a collection filled by hand.

The moments, in the order they fire:

| Operation | before | after |
|---|---|---|
| any write | `before_save` | `after_save` |
| …of a new aggregate | `before_create` | `after_create` |
| …of a stored one | `before_update` | `after_update` |
| `archive` | `before_archive` | `after_archive` |
| `remove` | `before_remove` | `after_remove` |
| a record read | — | `after_read` |
| a page answered | — | `after_search` |

`on(Invoice)` selects by `isinstance`, so a hook on a base class covers its subclasses, and
`on(object)` runs for every aggregate — an audit, a log. Replacing, extending, switching off and
ordering hooks — and why they work the way they do — are in [hooks.md](hooks.md).

## 9. Domain events

The aggregate **records** what happened; the Feature that saved it **publishes**. The
aggregate never publishes itself — a rollback would then undo a fact the world already heard.

```python
from sincpro_framework.ddd import DomainEvent


@dataclass(kw_only=True)
class InvoicePosted(DomainEvent):
    name = "billing.invoice.v1.posted"     # the wire name; keep it stable
    invoice_id: str = ""
    total: int = 0


def post(invoice: Invoice) -> None:
    invoice.state = "posted"
    invoice.record(InvoicePosted(invoice_id=invoice.id, total=invoice.total))
```

A subscriber is an ordinary Feature on another bounded context's bus, registered for the event
class. `Subscriber` executes every bus whose registry knows the event:

```python
from sincpro_framework.events import Publisher, Subscriber, SyncQueue

notifications = UseFramework("notifications", log_after_execution=False)
sent: list[str] = []


@notifications.feature(InvoicePosted)
class EmailTheCustomer(Feature):
    def execute(self, dto: InvoicePosted) -> None:
        sent.append(dto.invoice_id)


publisher = Publisher(SyncQueue(Subscriber(notifications)))

draft = repository.get_by(Invoice, number="F-004")
post(draft)
repository.save(draft)
for event in draft.pull_events():          # pulling hands them back and forgets them
    publisher.publish(event)

assert sent == [draft.id]
```

`SyncQueue` runs the subscribers in the same call. `BackgroundQueue(build_subscriber).start()`
runs them in another process, and Kafka or RabbitMQ are one more `Queue`. Nothing is wired by
default and nothing is stored — see [events](../events/README.md).

## 10. Change tracking

`ChangeTrackingMixin` records **one** `EntityUpdated` event per save, with every field that
changed as `(before, after)`. A field set and set back is no change.

```python
from sincpro_framework.ddd import ChangeTrackingMixin, EntityUpdated


@dataclass
class Payment(ChangeTrackingMixin, Entity):
    amount: int = 0
    method: str = "cash"
    receipt_cache: str = field(default="", metadata={"tracked": False})


payment_table = entity_table(
    "payment",
    billing.metadata,
    Column("amount", Integer),
    Column("method", Text),
    Column("receipt_cache", Text),
)
map_aggregates(billing, {Payment: payment_table})
billing.metadata.create_all(database.engine)

payment = Payment(amount=100)
repository.save(payment)

payment = repository.get(Payment, payment.id)
payment.amount = 120
payment.method = "qr"
payment.receipt_cache = "<html>…</html>"      # not tracked
repository.save(payment)

[updated] = payment.pull_events()
assert isinstance(updated, EntityUpdated)
assert updated.changes == {"amount": (100, 120), "method": ("cash", "qr")}
```

Publish it like any other event, or store it as an audit trail. More in
[change-tracking.md](../events/change-tracking.md).

## 11. Event sourcing

When the history *is* the product — a ledger, an audit — nothing is updated: writing is
appending a fact, and the state is what the facts add up to. A `DomainEvent` is an `Entity`, so
the same repository stores and queries it. `event_columns()` is the envelope every event
carries.

```python
from sincpro_framework.ddd import Sort
from sincpro_framework.orm import event_columns

ledger = registry()


@dataclass(kw_only=True)
class MoneyDeposited(DomainEvent):
    name = "bank.v1.money_deposited"
    account_id: str = ""
    amount: int = 0


@dataclass(kw_only=True)
class MoneyWithdrawn(DomainEvent):
    name = "bank.v1.money_withdrawn"
    account_id: str = ""
    amount: int = 0


class Deposits(EntityCollection[MoneyDeposited]): ...


class Withdrawals(EntityCollection[MoneyWithdrawn]): ...


facts = {
    MoneyDeposited: entity_table(
        "money_deposited", ledger.metadata, *event_columns(),
        Column("account_id", Text), Column("amount", Integer),
    ),
    MoneyWithdrawn: entity_table(
        "money_withdrawn", ledger.metadata, *event_columns(),
        Column("account_id", Text), Column("amount", Integer),
    ),
}
map_aggregates(ledger, facts)
ledger.metadata.create_all(database.engine)

account = "acc-1"
repository.save([
    MoneyDeposited(account_id=account, amount=500),
    MoneyDeposited(account_id=account, amount=300),
])
repository.save(MoneyWithdrawn(account_id=account, amount=200))

of_account = Criteria(
    where=Condition(field="account_id", value=account), order=(Sort(field="id"),)
)
balance = sum(fact.amount for fact in repository.fetch_all(Deposits, of_account)) - sum(
    fact.amount for fact in repository.fetch_all(Withdrawals, of_account)
)
assert balance == 600
```

Ordering by `id` is ordering by time: an id is a UUID v7. Rebuilding on every read does not
scale, so the answer is usually also kept as a projection — an ordinary aggregate saved beside
the facts. The costs: a fact is forever, so its shape is versioned in its `name` (`bank.v1.…`)
and a v2 is a new class; a correction is another fact; and append-only is a discipline — make
it a hook if it must be enforced:

```python
append_only = Hooks(None)


@append_only.on(DomainEvent)
class WrittenOnce(Hook):
    def before_save(self, fact: DomainEvent) -> None:
        if not fact.is_new:
            raise ContractViolation(f"{fact.name} is written once and never replaced")


facts_only = Repository(database, append_only)

correction = MoneyDeposited(account_id="acc-2", amount=10)
facts_only.save(correction)
correction.amount = 15
try:
    facts_only.save(correction)
    raise AssertionError("a fact must not be replaced")
except ContractViolation:
    pass
```

See [shapes.md §3](../shapes.md#3-the-facts-are-the-state).

## 12. An outbox

To deliver an event to another process without losing it when that process is down, store it
in the same transaction as the change, and let a relay deliver what is pending.
`EventTrackableMixin` gives an event its own delivery state; `delivery_columns()` adds it to the
table.

```python
from sincpro_framework.ddd import EventStatus, EventTrackableMixin
from sincpro_framework.orm import delivery_columns

outbox = registry()


@dataclass(kw_only=True)
class InvoiceSent(EventTrackableMixin, DomainEvent):
    name = "billing.invoice.v1.sent"
    invoice_id: str = ""


class Outbox(EntityCollection[InvoiceSent]): ...


outbox_table = entity_table(
    "outbox", outbox.metadata, *event_columns(), *delivery_columns(),
    Column("invoice_id", Text),
)
map_aggregates(outbox, {InvoiceSent: outbox_table})
outbox.metadata.create_all(database.engine)

with repository.context() as unit:                 # the change and its fact, together
    invoice = unit.get_by(Invoice, number="F-003")
    invoice.state = "sent"
    unit.save(invoice)
    unit.save(InvoiceSent(invoice_id=invoice.id))

delivered: list[str] = []
pending = Criteria(where=Condition(field="status", value=EventStatus.PENDING))
with repository.context() as unit:                 # the relay
    for fact in unit.search(Outbox, pending, for_update=True, skip_locked=True).items:
        delivered.append(fact.invoice_id)          # hand it to the broker here
        fact.mark_acknowledged()
        unit.save(fact)

assert delivered == [invoice.id]
assert repository.count(Outbox, pending).value == 0
```

`for_update` with `skip_locked` lets several relays run at once without claiming the same row
(on PostgreSQL; SQLite has one writer anyway). A delivery that fails is `mark_failed(reason)`,
and `attempts` caps the retries.

## 13. Testing

A Feature is tested through its bus with the adapters swapped. `MemoryRepository` answers the
same vocabulary — `save`, `get`, `search` with a `Criteria`, hooks — with no database, and
`override_dependencies` swaps it in for the length of the test:

```python
from sincpro_framework.ddd import MemoryRepository
from sincpro_framework.testing import override_dependencies


def test_registering_a_customer_stores_it():
    in_memory = MemoryRepository()

    with override_dependencies(billing_bus, repository=in_memory):
        answer = billing_bus(
            CommandRegisterCustomer(name="Eva", email="eva@acme.bo"), ResponseRegisterCustomer
        )

    assert in_memory.get(Customer, answer.customer_id).name == "Eva"


test_registering_a_customer_stores_it()
```

`MemoryRepository(*records)` starts with records already in it. What only a database can
prove — a unique index, a foreign key, a lock — needs the real one; see
[testing.md](testing.md) for the suite that runs this layer under volume and concurrency.

## 14. Extending an aggregate

A subclass of an aggregate — an addon's `CreditInvoice` that is an `Invoice` with a credit
reason — keeps what it inherits in the parent's table and only its own columns in its table,
which references the parent's key:

```python
from sqlalchemy import Table

credit = registry()
credit_invoice_table = entity_table(
    "credit_note",
    credit.metadata,
    Column("number", Text, nullable=False),
    Column("total", Integer, nullable=False),
)


@dataclass
class Note(Entity):
    number: str = ""
    total: int = 0


@dataclass
class CreditNote(Note):
    reason: str = ""


credit_reason_table = Table(
    "credit_note_reason",
    credit.metadata,
    Column("id", Text, ForeignKey("credit_note.id"), primary_key=True),
    Column("reason", Text, nullable=False),
)
map_aggregates(credit, {Note: credit_invoice_table, CreditNote: credit_reason_table})
credit.metadata.create_all(database.engine)

note = CreditNote(number="NC-1", total=50, reason="returned goods")
repository.save(note)
stored = repository.get(CreditNote, note.id)
assert (stored.number, stored.total, stored.reason) == ("NC-1", 50, "returned goods")
```

`map_aggregates` maps the parent first whatever the order, versions the extension like its
parent, and refuses a table that does not reference the parent's key. A `Criteria` filters and
orders by inherited and own fields alike.

---

## Where to read next

| Page | What it answers |
|---|---|
| [criteria.md](criteria.md) | The grammar of a `Criteria`, how two merge, what `dropped` means |
| [specification.md](specification.md) | What to bring back, at any depth |
| [relations.md](relations.md) | Every kind of relation and how each one is resolved |
| [lifecycle.md](lifecycle.md) | What runs, in which order, when something is written |
| [hooks.md](hooks.md) | Hooks in full: discovery, dependencies, what is refused |
| [reference.md](reference.md) | The adapter's whole surface, engines, mapping, observability |
| [decisions.md](decisions.md) | Why each piece is the way it is |
