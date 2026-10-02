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
| Record every field that changed, keep the events | [10. Change tracking, and the context's event table](#10-change-tracking-and-the-contexts-event-table) |
| Keep the facts as the state | [11. Event sourcing](#11-event-sourcing) |
| Deliver events to other contexts reliably | [12. Delivering events](#12-delivering-events) |
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

    def post(self) -> None:                # the rule lives on the aggregate it changes (§9)
        self.state = "posted"
        self.record(InvoicePosted(invoice_id=self.id, total=self.total))


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
  `updated_by`; `ChangeTrackingMixin` records what changed ([§10](#10-change-tracking-and-the-contexts-event-table)).

## 2. Tables and the repository

The table is declared once, in infrastructure. `entity_table` adds the four `Entity` columns;
`map_aggregates` ties each class to its table and infers the relations from the foreign keys.

```python
from sqlalchemy import Column, ForeignKey, Integer, Text
from sqlalchemy.orm import registry

from sincpro_framework.orm import (
    Database,
    Relation,
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
map_aggregates(
    billing,
    {Customer: customer_table, Invoice: invoice_table},
    # An invoice is an aggregate of its own: the customer reads them, never writes or removes them.
    relations={Customer: {"invoices": Relation.foreign_key(Invoice, identified_by="customer_id", owned=False)}},
)

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

**A repository per aggregate, when its questions deserve names.** The repository above answers
everything with a type and a `Criteria`, and most Features need nothing else. When the domain
keeps asking one aggregate the same questions, `AggregateRepository[T]` names them — a thin view
over the same repository, with the aggregate already given:

```python
from sincpro_framework.ddd import EntityCollection
from sincpro_framework.orm import DatabaseAggregateRepository


class InvoiceBook(DatabaseAggregateRepository[Invoice]):
    """Billing's questions about invoices, by their names."""

    def drafts_of(self, customer: Customer) -> EntityCollection[Invoice]:
        return self.search(Criteria.model_validate({
            "where": {"all": [
                {"field": "customer_id", "value": customer.id},
                {"field": "state", "value": "draft"},
            ]},
        }))


book = InvoiceBook(repository)
assert [invoice.number for invoice in book.drafts_of(ana).items] == ["F-004"]
assert book.measures(total=("sum", "total")) == {"total": 475}

with book.context() as unit:                  # the same class, bound to one transaction
    assert [invoice.number for invoice in unit.drafts_of(ana).items] == ["F-004"]
```

Every call goes through `repository` — hooks, scope, version check and named errors included —
and `context()` and `narrowed()` hand back the same class over the bound repository. It is
optional and never the only door: `book.repository` is the whole store, and `statement()` /
`run()` take a question past what `Criteria` says.

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

An aggregate is saved whole: a to-many tied by a foreign key is a part of its root — written by
`save`; what an assignment drops, or `remove` leaves, is deleted or detached as the relation
declares (`orphans=Orphans.DELETE` / `Orphans.DETACH`), and refused until it does. `Customer.invoices` is declared
`owned=False` above, because an invoice is an aggregate of its own: the customer reads them and
never writes or removes them. The rules are in
[relations.md §8](relations.md#8-an-aggregate-is-saved-whole).

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
class BillingEvent(DomainEvent):            # every event of this bounded context (§10)
    name = "billing.v1.event"


@dataclass(kw_only=True)
class InvoicePosted(BillingEvent):
    name = "billing.invoice.v1.posted"     # the wire name; keep it stable
    invoice_id: str = ""
    total: int = 0
```

`Invoice.post()` (declared with the aggregate in §1) changes the state and records
`InvoicePosted` — the rule lives on the aggregate it changes, never in a free function.

A subscriber is an ordinary Feature on another bounded context's bus, registered for the event
class. `Subscriber` executes every bus whose registry knows the event:

```python
from sincpro_framework.event_driven import Publisher, Subscriber, SyncQueue

notifications = UseFramework("notifications", log_after_execution=False)
sent: list[str] = []


@notifications.feature(InvoicePosted)
class EmailTheCustomer(Feature):
    def execute(self, dto: InvoicePosted) -> None:
        sent.append(dto.invoice_id)


publisher = Publisher(SyncQueue(Subscriber(notifications)))

draft = repository.get_by(Invoice, number="F-004")
draft.post()
repository.save(draft)
for event in draft.pull_events():          # pulling hands them back and forgets them
    publisher.publish(event)

assert sent == [draft.id]
```

`SyncQueue` runs the subscribers in the same call. `BackgroundQueue(build_subscriber).start()`
runs them in another process, and Kafka or RabbitMQ are one more `Queue`. Nothing is wired by
default, and nothing is kept until the context gives its events a table (§10) — see
[events](../events/README.md).

## 10. Change tracking, and the context's event table

`ChangeTrackingMixin` records **one** `EntityUpdated` event per save, with every field that
changed as `(before, after)`. A field set and set back is no change. `change_event` names the
class it records — here one of the context's own, so it is kept with the rest:

```python
from typing import ClassVar

from sincpro_framework.ddd import ChangeTrackingMixin, EntityUpdated


@dataclass(kw_only=True)
class PaymentUpdated(EntityUpdated, BillingEvent):
    name = "billing.payment.v1.updated"


@dataclass
class Payment(ChangeTrackingMixin, Entity):
    change_event: ClassVar[type[DomainEvent]] = PaymentUpdated
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

**A domain event is an entity.** It has an id (a UUID v7, so ordering by it is ordering by
time), it is saved and searched through the repository like any other, and every event of the
bounded context lives in **one table**: the context declares it with the `event_table`
template and maps its base class to it. Each subclass goes to the same table — its wire `name`
tells the rows apart — and what a subclass adds is kept in a `payload` column:

```python
from sincpro_framework.ddd.criteria import combined, parse_order
from sincpro_framework.orm import event_table, map_events

billing_events = event_table("billing_events", billing.metadata)
map_events(billing, BillingEvent, billing_events)       # BillingEvent and every subclass
billing.metadata.create_all(database.engine)

payment = repository.get(Payment, payment.id)
payment.amount = 130
repository.save(payment)                    # the payment, and its PaymentUpdated, one commit

about_the_payment = Criteria(
    where=Condition(field="entity_id", value=payment.id), order=parse_order("id")
)
[kept] = repository.fetch_all(PaymentUpdated, about_the_payment).items   # its history
assert kept.changes == {"amount": (120, 130)}
assert [type(one) for one in repository.fetch_all(BillingEvent, about_the_payment).items] == [
    PaymentUpdated
]
```

| Column | What it is |
|---|---|
| `id`, `name`, `created_at` | the event: who it is, which class, when |
| `entity_type`, `entity_id`, `entity_version` | what it is about — empty for an event about no entity |
| `correlation_id`, `causation_id`, `label` | the envelope every `DomainEvent` carries |
| `payload` | what the subclass declares, as JSON, typed back on load |
| `is_deliverable`, `delivered_at`, `next_delivery_at`, `delivery` | where a deliverable event's delivery stands (§12) |

What this gives, with nothing else to learn:

- **Kept with the change.** `save(aggregate)` writes the events it recorded in the same
  transaction; a rollback keeps neither. A save repeated keeps an event once.
- **Read, never taken.** The aggregate still has them: `pull_events()` hands them to whoever
  publishes by hand (§9).
- **An event about nothing** — `repository.save([DayClosed()])` — is kept the same way, with
  `entity_type` and `entity_id` empty.
- **Queried like any entity.** `fetch_all(PaymentUpdated, criteria)` answers that class;
  `fetch_all(BillingEvent, criteria)` the whole context, each row as its class.

A project with no table for its events loses nothing: they are recorded, pulled and published
as in §9. More in [change-tracking.md](../events/change-tracking.md).

## 11. Event sourcing

Keeping the events beside the state is *event storing*: the payment still has its row. When the
history *is* the state — a ledger, an account — the entity keeps no row: `EventSourcedMixin`
makes its state what its events add up to. Each event it records is applied at once and numbered
in the entity's history (`entity_version` 1, 2, 3 …); `repository.get` rebuilds it from them and
`repository.save` appends the new ones.

```python
from sincpro_framework.ddd import EventSourcedMixin


@dataclass(kw_only=True)
class MoneyDeposited(BillingEvent):
    name = "billing.account.v1.money_deposited"
    amount: int = 0


@dataclass(kw_only=True)
class MoneyWithdrawn(BillingEvent):
    name = "billing.account.v1.money_withdrawn"
    amount: int = 0


@dataclass
class CustomerAccount(EventSourcedMixin, Entity):
    event_base = BillingEvent                      # where its events are read from
    balance: int = 0

    def deposit(self, amount: int) -> None:
        self.happened(MoneyDeposited(amount=amount))

    def withdraw(self, amount: int) -> None:
        if amount > self.balance:
            raise ContractViolation("not enough money in the account")
        self.happened(MoneyWithdrawn(amount=amount))

    def apply(self, event: DomainEvent) -> None:   # how each event moves the state
        match event:
            case MoneyDeposited():
                self.balance += event.amount
            case MoneyWithdrawn():
                self.balance -= event.amount


account = CustomerAccount(id="acc-1")
account.deposit(500)
account.deposit(300)
repository.save(account)                           # two events; no row for the account

account = repository.get(CustomerAccount, "acc-1")  # rebuilt from them, in order
account.withdraw(200)
repository.save(account)

again = repository.get(CustomerAccount, "acc-1")
assert (again.balance, again.version) == (600, 3)
```

**Two writers never both win.** `(entity_type, entity_id, entity_version)` is unique in the
table, so of two copies rebuilt at the same version the second to save finds its number taken:

```python
mine = repository.get(CustomerAccount, "acc-1")
theirs = repository.get(CustomerAccount, "acc-1")
mine.deposit(10)
repository.save(mine)
theirs.withdraw(10)
try:
    repository.save(theirs)
    raise AssertionError("the second writer must rebuild first")
except StaleAggregate:
    pass
```

The costs are the usual ones. An event is forever, so its shape is versioned in its `name`
(`billing.account.v1.…`) and a v2 is a new class; a correction is another event. Rebuilding on
every read grows with the history: a snapshot is the next iteration, and until then a read model
is an ordinary aggregate saved beside the events. Replaying a history to another system is the
broker's job (Kafka keeps it), not a second store. See
[shapes.md §3](../shapes.md#3-the-facts-are-the-state).

## 12. Delivering events

Publishing after the commit can lose an event: the process can die between the two. The
transactional outbox closes that gap, and here it is the event table itself: an event marked
`DeliverableEventMixin` is kept with the change, in the same transaction, with where its
delivery stands beside it; an `EventRelay` reads the ones not delivered yet, hands each one on,
and marks it.

```python
from sincpro_framework.ddd import DeliverableEventMixin
from sincpro_framework.event_driven import EventRelay


@dataclass(kw_only=True)
class InvoiceSent(DeliverableEventMixin, BillingEvent):    # it leaves the context
    name = "billing.invoice.v1.sent"                        # a contract: explicit, versioned
    invoice_id: str = ""


with repository.context() as unit:
    invoice = unit.get_by(Invoice, number="F-003")
    invoice.state = "sent"
    invoice.record(InvoiceSent(invoice_id=invoice.id))
    unit.save(invoice)                                 # the invoice and its event, one commit

shipping = UseFramework("shipping", log_after_execution=False)
shipped: list[str] = []


@shipping.feature(InvoiceSent)
class ShipTheInvoice(Feature):
    def execute(self, dto: InvoiceSent) -> None:
        shipped.append(dto.invoice_id)


relay = EventRelay(
    repository,
    BillingEvent,                                       # the context's events …
    Publisher(SyncQueue(Subscriber(shipping))),         # … to a queue: or FastStreamQueue(kafka)
)
done = relay.run_once()

assert (done.read, done.delivered) == (1, 1)
assert shipped == [invoice.id]
assert relay.run_once().read == 0                       # delivered once

[sent] = repository.fetch_all(InvoiceSent).items
assert sent.delivered_at is not None
assert "delivered_at" not in sent.as_json()             # its delivery is never sent with it
```

**Delivery is the event's own state**, not a reader's checkpoint: three fields the mixin adds.

| Field | Kept as | What it says |
|---|---|---|
| `delivered_at` | column | when it went out — empty while it has not |
| `next_delivery_at` | column | when the relay may try it — at once when it is made, later after a failure, empty once parked |
| `delivery` | JSON | for people: `{"attempts": 2, "last_error": "…", "parked": true}` |

`is_deliverable` is a column too, so every question is a `Criteria` over the same repository —
what is pending, what was parked, what one invoice sent:

```python
pending = Criteria(
    where=combined(
        Condition(field="is_deliverable", value=True),
        Condition(field="delivered_at", operator=Operator.IS_NULL, value=True),
    )
)
assert repository.count(BillingEvent, pending).value == 0
```

What one pass does:

| Step | What happens |
|---|---|
| claim | the deliverable events of `source` not delivered and due, oldest first, at most `batch` — inside a unit of work, `FOR UPDATE SKIP LOCKED` where the database has it, so replicas never take the same event |
| hand on | each one to the publisher, in order; a failure is the policy's to decide |
| mark | `delivered_at`, or the retry, park or skip — saved in the same transaction |

**What a failure does is a strategy.** The default, `RetryInPlace(attempts=5)`, tries the event
again with exponential backoff and holds everything behind it, then parks it and goes on:

| Policy | An event that fails |
|---|---|
| `RetryInPlace(attempts, backoff, then=…, never_retry=…)` | tried again on the next pass; nothing after it goes first |
| `RetryLater(attempts, backoff, then=…, holds_stream=True)` | tried again at its time; the rest go on, the later events of its entity wait behind it |
| `ParkAndContinue()` | kept aside with its reason; `event.replayed()` and a save put it back |
| `SkipAndContinue()` | marked delivered with a log line and the reason in `delivery` |

`ExponentialBackoff` and `FixedBackoff` space the attempts; `never_retry=(ValueError, …)` parks
at once an error that no retry will fix.

**At least once, never lost.** A relay that dies after handing an event on and before the commit
leaves it unmarked, and the next pass sends it again — a consumer is idempotent (the
[inbox](../events/brokers.md)). An event no aggregate records is kept the same way through
`Publisher(RepositoryQueue(repository))`. Run the relay from a cron:

```python
from datetime import timedelta

from sincpro_framework.cron import Crons

delivery = Crons("delivery")
delivery.run_relay(relay, every=timedelta(seconds=5))
```

`delivery.relay_deliverable_events(repository=…, source=BillingEvent, to=Publisher(…))` builds
the relay and schedules it in one line. The model and why it is this way are in
[PRD_19](../prd/PRD_19_events-as-entities.md) and
[decision 31](decisions.md#31-a-bounded-contexts-events-are-entities-in-one-table).

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
