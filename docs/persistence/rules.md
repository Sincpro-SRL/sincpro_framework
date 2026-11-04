# Rules and computations: `@check` and `@compute`

An aggregate holds rules over its own fields — a confirmed order needs a delivery date, a line
sells at least one unit, a product appears once — and values it computes from others — a line's
amount, the total, the payment term a customer proposes. Each is written once, on the method of
the entity that answers it. The framework decides when it runs, and where it runs decides what a
broken rule does: while a form is edited it warns, when the record is saved it refuses.

| Kind of rule | Example | Where it goes | When it runs |
|---|---|---|---|
| One value | a positive amount, a valid tax id | a `ValueObject` or the field's type | when the value is built |
| The aggregate's own fields | a confirmed order has a delivery date; a product appears once | **`@check`** on the entity | every save; while a form is edited, as a warning |
| Something outside the record | the customer has credit; the repositories it names exist | a hook (`before_save`), which has the bus | every save |
| An action | confirm, share, change tenant | a Command and the entity's method | that action only |
| A value computed from others | a line's amount, the total | **`@compute`** | every save; while a form is edited, what the change reaches |
| A value proposed from others | the payment term of the customer picked | **`@compute(editable=True)`** | only while a form is edited, when the change reaches it |
| A value the database computes | a balance, total − paid | `computed(sql=…, python=…)` | when the row is read |

## The sale order

```python
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum

from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import Entity, check, compute, read_only, recompute
from sincpro_framework.ddd import exceptions

PRICES = {"desk": Decimal("100"), "chair": Decimal("40")}
TERMS = {"acme": "30 days", "walk-in": "cash"}


class SaleState(StrEnum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"


@dataclass
class SaleLine:
    product: str = ""
    qty: int = 1
    price: Decimal = Decimal("0")
    amount: Decimal = Decimal("0")

    @compute(lambda line: line.price, depends=lambda line: line.product, editable=True)
    def price_of(self) -> Decimal:
        return PRICES.get(self.product, Decimal("0"))

    @compute(lambda line: line.amount, depends=lambda line: [line.qty, line.price])
    def amount_of(self) -> Decimal:
        return self.qty * self.price

    @check(lambda line: line.qty)
    def sells_something(self) -> None:
        if self.qty < 1:
            raise exceptions.InvalidValue("A line sells at least one unit")


@dataclass
class Sale(Entity):
    customer: str = ""
    payment_term: str = ""
    state: SaleState = SaleState.DRAFT
    delivery: date | None = None
    lines: list[SaleLine] = field(default_factory=list)
    total: Decimal = Decimal("0")

    @compute(lambda s: s.payment_term, depends=lambda s: s.customer, editable=True)
    def payment_term_of(self) -> str:
        return TERMS.get(self.customer, "")

    @compute(lambda s: s.total, depends=lambda s: s.lines)
    def total_of(self) -> Decimal:
        return sum((line.amount for line in self.lines), Decimal("0"))

    @check(lambda s: [s.delivery, s.state])
    def delivery_when_confirmed(self) -> None:
        if self.state == SaleState.CONFIRMED and self.delivery is None:
            raise exceptions.InvalidValue("A confirmed sale needs a delivery date")

    @check(lambda s: s.lines)
    def each_product_once(self) -> None:
        seen: set[str] = set()
        for position, line in enumerate(self.lines):
            if line.product in seen:
                raise exceptions.InvalidValue(
                    f"{line.product} is already on the sale", ("lines", position, "product")
                )
            seen.add(line.product)
```

Each lambda names fields through the entity and runs once, when the class is described: a name
that is not a field, a field computed by two methods, a field computed from itself and a cycle
are refused there, naming the method. A method can be on any dataclass — a line that is not an
`Entity` declares its own.

- **`@compute(field, depends=…)`** — the method answers the field's new value. `depends` is one
  field or a list; with none, only a save or a preview of the whole record computes it. The
  computations run in the order their dependencies need.
- **`@compute(…, editable=True)`** — a proposal, not a rule. It runs only when a change reaches
  what it depends on, and never when a save computes the record whole, so the value the user
  typed over it is the one stored. `Meta` publishes the field editable; a computed one, read-only.
- **`@check(watches)`** — the method raises `InvalidValue` when the rule is broken. With no
  path, the failure points at the first field it watches: list first the field the user fixes.
  With a path of its own it points anywhere inside the aggregate — a line's cell included.

## A save refuses every broken rule at once

```python
repository = MemoryRepository()
broken = Sale(
    customer="acme",
    state=SaleState.CONFIRMED,
    lines=[SaleLine("desk", 1, Decimal("100")), SaleLine("desk", 0, Decimal("100"))],
)

try:
    repository.save(broken)
except exceptions.ChecksFailed as refused:
    assert [one.path for one in refused.failures] == [
        ("lines", 1, "qty"),
        ("delivery",),
        ("lines", 1, "product"),
    ]
```

A save computes every computation that is not editable — the lines first — then runs every
check, then the `before_save` hooks. Nothing is written when a check fails: `ChecksFailed`
carries each failure, and every wire answers one field error per failure, each with its pointer
(`#/values/lines/1/qty`). It is an `InvalidValue`, so whoever handles one handles it.

| Write | Computations | Checks | Hooks |
|---|---|---|---|
| `save`, `Edit` with `SAVE`, any Command that saves | every one, not the editable | every one | `before_save` after them |
| `upsert` | none | none | `before_save` / `after_save` |
| `update_all`, `remove_all` | none | none | none |
| reads: `get`, `search`, a `DataFrame` | none — the stored value is read | none | `after_read` |

`upsert`, `update_all` and `remove_all` are the writes that skip the aggregate on purpose — an
idempotent import, a sync with another system, closing expired sessions. A value they change
leaves its computed fields as they were until the next `save`.

## A form is warned of what its change reaches

```python
sale = Sale(customer="acme", lines=[SaleLine("desk", 2), SaleLine("chair", 0)])

with read_only() as warnings:
    recompute(sale, ["state", "lines.1.qty"])

assert [one.path for one in warnings] == [("lines", 1, "qty")]
```

`PreviewEntity` and `Edit` with `SAVE_DRAFT` run `recompute` inside `read_only()` with the
fields the form says changed. Only what that change reaches runs:

- a computation that reads a changed field — and what reads that one, in turn;
- an editable computation, only so;
- a check that watches a field the change reached, which warns the form (`FormWarning`) and
  refuses nothing: a draft may be incomplete.

A form names a line's cell by its position in the list it sent: `lines.1.qty` runs the second
line's checks of `qty` and its editable computations of `qty`; `lines.1` runs every check of
that line; `lines` runs every check of every line. Nothing named — a form that just opened —
computes every computation that is not editable and warns of nothing.

Outside `read_only()`, `recompute` runs no check: the save checks them all.

## A value proposed while the form is typed

```python
sale = Sale(customer="acme", lines=[SaleLine("desk", 2), SaleLine("chair", 3)])

recompute(sale, ["customer", "lines.1.product"])
assert sale.payment_term == "30 days"
assert [line.price for line in sale.lines] == [Decimal("0"), Decimal("40")]

sale.payment_term = "60 days"  # typed over the proposal
repository.save(sale)
assert repository.get(Sale, sale.id).payment_term == "60 days"
```

The editable computation is the form's `onchange`: picking a customer proposes its term,
picking a product proposes its price. The save keeps what was typed.

## Lines and other aggregates

A record the aggregate owns — a line — declares its own checks and computations; each runs on
every line, its warnings and failures pointing at it (`("lines", 2, "price")`). A rule across
lines belongs to the aggregate, which sees them all (`each_product_once`). Another aggregate it
only points at — the tags of a many-to-many, a customer — is computed if it computes, but its
checks are its own save's: a stored tag that breaks a rule written after it never refuses the
note that names it.

A save never reads. A check that watches a relation the record does not hold whole — not loaded,
cut by a specification, assigned blind — is not judged: a rule over lines nobody read whole has
nothing to judge. Lines held in part may break it unseen, so that skip is logged with the way
out (read them whole). A relation never read cannot have changed, so that one is silent.

A rule that reads another aggregate or anything outside the record — the customer's credit,
repositories stored elsewhere, a call to another service — is not a check: it is a hook, which
has the bus and runs inside the transaction of the save. See [Hooks](hooks.md).

## A subclass, a module that extends

```python
@dataclass
class LenientSale(Sale):
    def delivery_when_confirmed(self) -> None:
        pass  # a counter sale has no delivery


repository.save(LenientSale(state=SaleState.CONFIRMED))
```

The declaration belongs to the method's name. A subclass that overrides the method without the
decorator keeps what it watches or computes and changes how; one that decorates it again
replaces the declaration. A module that extends an entity it does not own adds checks and
computations the same way — see [Extending an entity](extensions.md).

## An import skips the rules, out loud

```python
from sincpro_framework import UseFramework
from sincpro_framework.context import ContextKey

sales = UseFramework("sales", log_after_execution=False)

with sales.context({ContextKey.SKIP_CHECKS: "legacy import 2026-11", ContextKey.USER_ID: "ana"}):
    repository.save(Sale(state=SaleState.CONFIRMED, lines=[SaleLine("desk", 2, Decimal("10"))]))
```

An invariant is not switched off; what an import or a migration needs is to write rows that were
valid where they came from. `ContextKey.SKIP_CHECKS` holds why, as a sentence: the saves of that
call compute as always and skip the checks, and a warning says so once per execution, for each
entity, with the reason and the user — outside a bus execution, once per thread for the same
reason, so an import runs as a use case. A rule that depends on state is written in the check
itself (`delivery_when_confirmed`), never as a skip.

## The rows stored before a rule existed

```python
from sincpro_framework.ddd import violations_of

found = violations_of(repository, Sale, Sale.delivery_when_confirmed)
assert [(one.message, one.path) for one in found] == [
    ("A confirmed sale needs a delivery date", ("delivery",))
]
```

A rule added today does not change the rows written before it — nor the ones an import wrote
skipping it, like the sale above; the next save of each would refuse it. `violations_of(repository, Entity, Entity.method, criteria=None)` reads the records the
criteria names — every one by default — page by page, with the relations the checks watch, runs
that check over each exactly as a save would, and answers a `Violation` (`record_id`, `message`,
`path`) for each one that breaks it. Nothing is written; the list goes to a report or a
`DataFrame` as it is (`[one.model_dump() for one in found]`).

A rule a database must answer by itself — filtered, counted or grouped over millions of rows,
on a dashboard of what needs attention — is also a `computed` field, and the check reads the
same function, so it is written once in Python and once in SQL:

```python
from sincpro_framework.ddd import computed


def lacks_delivery(order: "Order") -> bool:
    return order.state == SaleState.CONFIRMED and order.delivery is None


@dataclass
class Order(Entity):
    state: SaleState = SaleState.DRAFT
    delivery: date | None = None
    missing_delivery: bool = computed(
        sql=lambda t: (t.state == "confirmed") & t.delivery.is_(None),
        python=lacks_delivery,
        default=False,
    )

    @check(lambda o: [o.delivery, o.state])
    def delivery_when_confirmed(self) -> None:
        if lacks_delivery(self):
            raise exceptions.InvalidValue("A confirmed order needs a delivery date")
```

`Condition(field="missing_delivery", value=True)` then filters, counts and groups in the
`SELECT`, in memory the same, and `ComputedFieldsContract` is the test that says the two agree
over the rows a store holds.

## Why not on every assignment

The checks run where the framework already passes — a save, a change in a form — and never on
an attribute being set:

1. **States in between are valid.** `confirm()` sets the state, then the delivery date: a rule
   judged after the first line would refuse a method that ends valid. An invariant holds when
   the command ends, which is the save.
2. **SQLAlchemy already instruments the mapped attributes.** A setter of the framework's would
   fight it and fire on every row a read loads.
3. **A dataclass's `__init__` assigns too**, so a half-built record would be judged.

## Performance

A check is a method call on a record already in memory. Measured in plain Python (CPython 3.12,
a laptop), one million sales of two lines each:

| | Time |
|---|---|
| building the million aggregates | 1.49 s |
| two checks over each of them | 0.49 s — 0.25 µs a check |

A save is dominated by the database round trip, never by its checks. A read runs none: a
`@compute` field is a stored column, a `computed(sql=…)` one is computed by the `SELECT`, and a
million rows go to a `DataFrame` (see [Analytics](analytics.md)) at the cost of reading them.
A change in a form runs only what it reaches.

## What is left out

- **A check that reads other aggregates.** A hook, with the bus, inside the save's transaction.
- **Enforcing the form hints.** `readonly`, `required` and the `*_when` of `presentation` are
  what a screen shows; a rule the server holds is a check.
- **A switch per rule.** Skipping is per call, with a reason, for imports and migrations only.
- **A check as a filter.** A filter names fields; a rule the database answers is a `computed`
  field the check reads, as above.
- **Validation on assignment.** See above.
