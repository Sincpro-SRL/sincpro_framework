# Recipe: rules and computations on the entity — `@check`, `@compute`

Use it when an aggregate holds a rule over its own fields (a confirmed order needs a delivery
date, a product appears once on a sale) or computes / proposes a value from others (a total, the
payment term of the customer picked). Full page: `docs/persistence/rules.md`.

## Where each rule goes

| Rule | Goes in |
|---|---|
| one value (positive amount, valid tax id) | a `ValueObject` / the field's type |
| over the aggregate's own fields, lines included | `@check` on the entity (or on the line class) |
| reads another aggregate, the bus, an external service | a hook `before_save` |
| an action (confirm, share, change tenant) | a Command + the entity's method |
| what a screen shows (readonly, required, visible) | `presentation` — hints, never enforced |

## Check or hook — one question

Can the rule be judged with the record alone? → `@check` (domain; no dependencies; the form
sees it while typing). Needs a dependency, the caller or another aggregate? → hook
`before_save` (application; `self.<dep>`, `self.context`, `self.bus`; inside the save's
transaction; ordered with `after=`/`sequence=`; gated). Save order: computations → checks →
`before_save` hooks. Hooks do not run in drafts/previews and are not skipped by
`SKIP_CHECKS` (a hook or a gate reads the key to skip too).

## Declare

```python
from sincpro_framework.ddd import Entity, check, compute
from sincpro_framework.ddd import exceptions

@dataclass
class SaleLine:
    product: str = ""
    qty: int = 1
    price: Decimal = Decimal("0")
    amount: Decimal = Decimal("0")

    @compute(lambda l: l.price, depends=lambda l: l.product, editable=True)   # onchange
    # A body that is one `return` of + - * over own fields, or sum(x.f for x in self.lines),
    # is published as a formula (x-sincpro.computes): the browser computes it offline.
    def price_of(self) -> Decimal:
        return PRICES.get(self.product, Decimal("0"))

    @compute(lambda l: l.amount, depends=lambda l: [l.qty, l.price])
    def amount_of(self) -> Decimal:
        return self.qty * self.price

    @check(lambda l: l.qty)
    def sells_something(self) -> None:
        if self.qty < 1:
            raise exceptions.InvalidValue("A line sells at least one unit")

@dataclass
class Sale(Entity):
    state: SaleState = SaleState.DRAFT
    delivery: date | None = None
    lines: list[SaleLine] = field(default_factory=list)

    @check(lambda s: [s.delivery, s.state])          # first = the field the error points at
    def delivery_when_confirmed(self) -> None:
        if self.state == SaleState.CONFIRMED and self.delivery is None:
            raise exceptions.InvalidValue("A confirmed sale needs a delivery date")

    @check(lambda s: s.lines)                        # across lines: on the aggregate
    def each_product_once(self) -> None:
        ...  raise exceptions.InvalidValue("…", ("lines", position, "product"))
```

## When it runs

| Path | Computations | Editable | Checks |
|---|---|---|---|
| `save` (any Command, `Edit` SAVE) | all, lines first | never | all → `ChecksFailed` (one `FieldError` each), nothing written |
| `PreviewEntity`, `Edit` SAVE_DRAFT (`recompute` in `read_only()`) | what `changed` reaches | when `changed` reaches its depends | those `changed` reaches → `FormWarning` |
| nothing changed yet (form opened) | all but editable | no | none |
| `recompute` outside `read_only()` | as above | as above | none (the save checks) |
| `upsert` / `update_all` / `remove_all` / reads | none | none | none |

`changed` names a line's cell by the line's identity — `"lines.0192….qty"`; by its position,
`"lines.1.qty"`, for a line with none —; `"lines.0192…"` = every check of that line; `"lines"` =
every line; a path that names no line is refused. A check over a relation not held whole is not judged on save
(warned when held in part). Records of another aggregate it points at (many-to-many,
reference) are not judged by this save — only the records the save writes. A subclass overriding the method without the decorator keeps the
declaration and changes the body.

## Imports and migrations

```python
with bus.context({ContextKey.SKIP_CHECKS: "legacy import 2026-11"}):
    repository.save(record)        # computes, skips checks; one warning per execution with reason + user
```

## Rows stored before the rule

```python
found = violations_of(repository, Sale, Sale.delivery_when_confirmed)   # [Violation(record_id, message, path)]
```

Pages through the store with the watched relations, runs the check, writes nothing. For SQL
filter/count/group over many rows, add a `computed(sql=…, python=fn)` boolean field and have the
check call the same `fn`; `ComputedFieldsContract` proves sql == python.

## Don't

- A check that reads another aggregate or the bus → a hook.
- Enforce `presentation` hints on the server → write a check.
- Validate on assignment / a proxy → states in between are valid; the save is the boundary.
