# Rules and computations: `@check` and `@compute`

An aggregate holds rules over its own fields — a confirmed order needs a delivery date, a line
sells at least one unit, a product appears once — and values it computes from others — a line's
amount, the total, the payment term a customer proposes. Each is written once, on the method of
the entity that answers it. The framework decides when it runs, and where it runs decides what a
broken rule does: while a form is edited it warns, when the record is saved it refuses.

| Kind of rule | Example | Where it goes | When it runs |
|---|---|---|---|
| One value | a positive amount, a valid tax id | a `ValueObject` — `holds=lambda v: v > 0` publishes it for a client too — or the field's type | when the value is built |
| The aggregate's own fields | a confirmed order has a delivery date; a product appears once | **`@check`** on the entity | every write — a save, an upsert; a write by criteria that reaches it is refused — and while a form is edited, as a warning ([Every write](#every-write-keeps-the-rules-or-says-it-skips-them)) |
| Something outside the record | the customer has credit; the repositories it names exist | a hook (`before_save`), with its dependencies, the context and the bus — [Checks and hooks](#checks-and-hooks) | every save |
| An action | confirm, share, change tenant | a Command and the entity's method | that action only |
| A value computed from others | a line's amount, the total | **`@compute`** | every save and upsert; while a form is edited, what the change reaches |
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
- **A computation simple enough runs in the browser too.** When the method's body is one
  `return` of `+`, `-`, `*` over the record's own fields and literal numbers, or a `sum(… for one
  in self.lines)` of such, the metamodel writes it down as a formula and the contract publishes
  it (`x-sincpro.computes`): a client computes the field as the server does — exactly, decimal
  places included (`valueOf` in `@sincpro/criteria`) — and asks the server only for what it cannot
  compute. Anything else — a division, a condition, a read of another service — has no formula,
  and the server computes it in a preview, as before.
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
else:
    raise AssertionError("the save let the broken sale through")
```

A save computes every computation that is not editable — the lines first — then runs every
check, then the `before_save` hooks. Nothing is written when a check fails: `ChecksFailed`
carries each failure, and every wire answers one field error per failure, each with its pointer
(`#/values/lines/1/qty`). It is an `InvalidValue`, so whoever handles one handles it.

A save is one of the writes. What every one of them does with the rules is the next section.

## Every write keeps the rules, or says it skips them

A rule is judged at one of three levels, and nowhere else:

| Level | Where | What runs | A broken rule |
|---|---|---|---|
| **The value** | building a `ValueObject` (`validate_fn`, `holds=`) | that value's own rule | `ValueError`, there and then |
| **The form** | `PreviewEntity`, `Edit` with `SAVE_DRAFT` — `recompute` inside `read_only()` | the computations and checks the change reaches | a `FormWarning` with `blocks_save`; nothing is refused, a draft may be incomplete |
| **The write** | every write of the repository, below | computations, then checks, then the `before_save` hooks | refused before anything is written |

Building an entity, or assigning one of its fields, judges nothing ([why](#why-not-on-every-assignment)).
Every write of the repository judges what it writes:

| Write | Computations | Checks | Hooks | A rule it cannot judge |
|---|---|---|---|---|
| `save` — and `Edit` with `SAVE`, any Command that saves | every one, the lines first, not the editable | every one | `before_save` after them | over a relation the record does not hold whole: not judged, logged when held in part |
| `upsert` | every one, over the row it writes | every one, over that row | `before_save` / `after_save` | **refused**: a rule over a field a conflict keeps as stored, or over records a row never carries |
| `update_all` | none — one statement holds no record | none | none | **refused**: a value a check watches, a computation reads, or a computed field set by hand |
| `remove_all` of an aggregate's rows | — | — | none | nothing to judge: no row is left to break its own rules |
| `upsert` / `update_all` / `remove_all` of the rows **another aggregate owns** (its lines) | as above | as above | as above | **refused**: a rule of the owner over that relation — a total of the lines, a check over them |
| reads: `get`, `search`, a `DataFrame` | none — the stored value is read | none | `after_read` | — |

**An upsert judges the record as it is handed**, which is the row it leaves either way: an insert
writes all of it, and a conflict overwrites what `update=` names — every field but the key and the
framework's own when it names nothing. A rule that reads fields of both kinds — some overwritten,
some a conflict keeps as stored — would be judged on values the row will not hold, so the call is
refused. So is a rule over records a row never carries — the lines of a one2many, the pairs of a
many2many —, and a record that holds such records: the upsert would drop them without a word.

**A write by criteria holds no record**, so any rule its values reach is refused rather than
skipped: a check that watches a field it sets, a computation that reads one — the computed field
would keep a value that no longer matches —, a computed field set by hand. A value no rule reads
is written as before: a note, a stamp, a closed session.

**Each refusal is a `ProgrammingError`**, never a refusal of the values: it depends on the call and
the entity, never on the rows, so the first test that makes the call sees it. It names every rule
and the three ways out:

1. write every field the rule reads — `update=` naming them, in an upsert;
2. save the records instead — read them, change them, save them, a page per flush — so each one
   is computed and judged;
3. skip the checks for that call, with a reason ([below](#an-import-skips-the-rules-out-loud)).

```python
from datetime import date

from sincpro_framework.ddd import Condition, Criteria
from sincpro_framework.exceptions import ProgrammingError

counter = Sale(customer="walk-in", lines=[SaleLine("chair", 2, Decimal("40"))])
repository.upsert(counter, on=("id",))  # computed and judged, as a save
assert repository.get(Sale, counter.id).total == Decimal("80")

try:
    repository.upsert(Sale(customer="walk-in", state=SaleState.CONFIRMED), on=("id",))
except exceptions.ChecksFailed as refused:
    assert [one.path for one in refused.failures] == [("delivery",)]
else:
    raise AssertionError("the upsert let the broken sale through")

try:
    repository.upsert(
        Sale(id=counter.id, state=SaleState.CONFIRMED, delivery=date(2026, 12, 1)),
        on=("id",),
        update=("state",),  # a conflict would keep the stored delivery
    )
except ProgrammingError as refused:
    assert "Sale.delivery_when_confirmed watches delivery" in str(refused)
else:
    raise AssertionError("the upsert judged a row it does not decide")

walk_in = Criteria(selection=Condition(field="customer", value="walk-in"))
try:
    repository.update_all(Sale, walk_in, {"state": SaleState.CONFIRMED})
except ProgrammingError as refused:
    assert "update_all of Sale cannot judge its rules" in str(refused)
else:
    raise AssertionError("update_all wrote past a check")
assert repository.update_all(Sale, walk_in, {"payment_term": "cash"}) == 1  # no rule reads it
```

The last write sets a field only an editable computation proposes: a value typed over a proposal
is what a save stores too, so nothing is reached.

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

A form names a line's cell by the line's identity — `lines.0192….qty` —, or by its position in
the list it sent when the line is a plain value with none, as here: `lines.1.qty` runs the second
line's checks of `qty` and its editable computations of `qty`; `lines.1` runs every check of
that line; `lines` runs every check of every line; a path that names no line is refused. Nothing named — a form that just opened —
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
every line, its warnings and failures pointing at it — by its identity when it is an entity
(`("lines", "0192…", "price")`), by its position when it is a plain value. A rule across
lines belongs to the aggregate, which sees them all (`each_product_once`). Another aggregate it
only points at — the tags of a many-to-many, a customer — is computed if it computes, but its
checks are its own save's: a stored tag that breaks a rule written after it never refuses the
note that names it.

A save never reads. A check that watches a relation the record does not hold whole — not loaded,
cut by a specification, assigned blind — is not judged: a rule over lines nobody read whole has
nothing to judge. Lines held in part may break it unseen, so that skip is logged with the way
out (read them whole). A relation never read cannot have changed, so that one is silent.

## Checks and hooks

A check is the domain's: a method of the entity over the fields it holds, with nothing injected,
so it runs anywhere the record is — a save, a draft, a preview, a test with no bus. A hook is
the application's: a class around the aggregate's writes and reads with everything a Feature of
its bus has.

```python
from sincpro_framework import UseFramework
from sincpro_framework.context import ContextKey
from sincpro_framework.ddd.repositories import Hook, Hooks

sales = UseFramework("sales", log_after_execution=False)


class CreditBook:
    def allows(self, customer: str, amount: Decimal) -> bool:
        return customer != "walk-in" or amount <= Decimal("500")


sales_hooks = Hooks(None)


@sales_hooks.on(Sale)
class CreditLimit(Hook):
    def before_save(self, sale: Sale) -> None:
        if not self.credit.allows(sale.customer, sale.total):  # a dependency of the bus
            raise exceptions.InvalidValue(
                f"{sale.customer} has no credit for {sale.total} "
                f"(asked by {self.context.get(ContextKey.USER_ID)})",
                ("customer",),
            )


sales.add_dependency("credit", CreditBook())
guarded = MemoryRepository(hooks=sales_hooks.inject(sales))

with sales.context({ContextKey.USER_ID: "ana"}):
    guarded.save(Sale(customer="walk-in", lines=[SaleLine("desk", 2, Decimal("100"))]))
    try:
        guarded.save(Sale(customer="walk-in", lines=[SaleLine("desk", 9, Decimal("100"))]))
    except exceptions.InvalidValue as refused:
        assert "no credit for 900" in str(refused) and "ana" in str(refused)
    else:
        raise AssertionError("the hook let the sale through")
```

| | `@check` | Hook |
|---|---|---|
| Layer | domain: the entity's own method | application: a class registered on the bounded context's `Hooks` |
| Reads | the fields of the aggregate and the lines it writes | anything — another aggregate, another context, the caller |
| Has | the record | `self.<dependency>`, `self.context` (the user, the tenant, the language), `self.bus` to run a Query or a Command |
| Runs | before a save or an upsert; while a form is edited, as a warning | at its moments: `before_save`, `after_save`, `before_create`/`update`, `before_remove`, `before_archive`, `after_read`, `after_search`… |
| In a draft or a preview | warns | does not run: nothing is written there |
| Order and control | declaration order | `after=`, `before=`, `sequence=`, gates (`AccessControl`), `replaces=` |
| Refusal | `ChecksFailed`, every failure at once | whatever it raises, the first one |
| `SKIP_CHECKS` | skipped | runs — a hook reads the key from `self.context`, or a gate does, to skip as well |

A save runs the computations, then the checks, then the `before_save` hooks — so a hook is handed
an aggregate that already holds its own rules and judges only what lies outside it — and inside
the save's transaction, so a hook that refuses rolls everything back. A hook never writes back
through the repository that fired it (it would fire itself forever); it runs a Command through
`self.bus`, or writes through another repository. Everything about hooks: [Hooks](hooks.md).

Which one, in one question: **can the rule be judged with the record alone?** Then it is a check,
and the form sees it while it is typed. Does it need a dependency, the caller or another
aggregate? Then it is a hook, and it answers on save.

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
with sales.context({ContextKey.SKIP_CHECKS: "legacy import 2026-11", ContextKey.USER_ID: "ana"}):
    repository.save(Sale(state=SaleState.CONFIRMED, lines=[SaleLine("desk", 2, Decimal("10"))]))
```

An invariant is not switched off; what an import or a migration needs is to write rows that were
valid where they came from. `ContextKey.SKIP_CHECKS` holds why, as a sentence: the saves of that
call compute as always and skip the checks, and a warning says so once per execution, for each
entity, with the reason and the user — outside a bus execution, once per thread for the same
reason, so an import runs as a use case. A rule that depends on state is written in the check
itself (`delivery_when_confirmed`), never as a skip.

The same key lets every write through — it is the one switch, and there is no other:

```python
with sales.context(
    {ContextKey.SKIP_CHECKS: "delivery dates of the old system", ContextKey.USER_ID: "ana"}
):
    assert repository.update_all(Sale, walk_in, {"delivery": date(2026, 12, 1)}) == 1
```

| Write | With `SKIP_CHECKS` |
|---|---|
| `save` | computes as always, judges no check |
| `upsert` | computes what its row decides, judges no check; a rule it cannot judge is let through |
| `update_all`, `remove_all` | a rule they reach is let through; a computed field they make stale keeps its stored value |

Each is said once per execution and entity, naming the reason and who asked; a write past rules
it could not judge names them too — `Sale written by update_all past its rules — Sale.delivery_
when_confirmed watches delivery: delivery dates of the old system (asked by ana)`. What the switch
never lets through is what is not a rule: an upsert of a record that holds lines is still refused,
because it would drop them, and the write's own refusals — a page, a column the framework sets —
stand.

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
- **Judging a write by criteria row by row.** It would read every row it writes, which is what a
  save already does, a page per flush; `update_all` is for the values no rule reads, and refuses
  the rest.
- **A check as a filter.** A filter names fields; a rule the database answers is a `computed`
  field the check reads, as above.
- **Validation on assignment.** See above.
