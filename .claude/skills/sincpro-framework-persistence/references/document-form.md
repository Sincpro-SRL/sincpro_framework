# Recipe: a document with lines served to a form — invoice, order, purchase

Use it when a screen, a mobile app or an agent edits a document that owns lines: the backend
declares the document once and answers every question the form asks with the built-in use cases.
Nothing here is specific to a client: the form sends the record as JSON and the server answers
what the record becomes.

## 1. The document declares what it computes, shows and refuses

```python
from dataclasses import dataclass, field
from decimal import Decimal

from sincpro_framework.ddd import (
    Condition, Entity, Presentation, When, check, compute, warn_form,
)
from sincpro_framework.ddd import exceptions
from sincpro_framework.ddd.criteria import MeasureFunction, Operator


@dataclass
class InvoiceLine(Entity):
    invoice_id: str = ""
    product_id: str = ""
    qty: Decimal = Decimal("1")
    price: Decimal = Decimal("0")
    cost: Decimal = Decimal("0")
    amount: Decimal = Decimal("0")

    @compute(lambda l: l.price, depends=lambda l: l.product_id, editable=True)   # onchange
    def price_of(self) -> Decimal:
        return price_list(self.product_id)

    @compute(lambda l: l.amount, depends=lambda l: [l.qty, l.price])
    def amount_of(self) -> Decimal:
        if self.price < self.cost:
            warn_form("price below cost", field="price")       # lands on this line, this cell
        return self.qty * self.price

    @check(lambda l: l.qty)
    def sells_something(self) -> None:
        if self.qty <= 0:
            raise exceptions.InvalidValue("A line sells at least one unit")   # warned, refused on save
    presentation = Presentation["InvoiceLine"](
        readonly=lambda l: l.amount,
        totals=lambda l: {l.amount: MeasureFunction.SUM},   # the foot of the lines
    )


@dataclass
class Invoice(Entity):
    partner_id: str
    number: str = "/"
    state: InvoiceState = InvoiceState.DRAFT
    lines: list[InvoiceLine] = field(default_factory=list)
    total: Decimal = Decimal("0")

    @compute(lambda i: i.total, depends=lambda i: i.lines)
    def total_of(self) -> Decimal:
        return sum((line.amount for line in self.lines), Decimal("0"))
    presentation = Presentation["Invoice"](
        readonly=lambda i: [i.number, i.state, i.total],
        readonly_when=lambda i: When(
            i.state != InvoiceState.DRAFT, i.partner_id, i.lines
        ),
    )
```

```python
from sincpro_framework.data_layer.orm import Orphans, TableRelation, map_aggregates

map_aggregates(registry, {Invoice: invoices, InvoiceLine: invoice_lines}, relations={
    Invoice: {"lines": TableRelation.foreign_key(
        InvoiceLine, identified_by="invoice_id", orphans=Orphans.DELETE)},
})
```

| What the form needs | Where it is declared | What the client receives |
|---|---|---|
| a line's amount, the total | `@compute` on each class | `values` of a preview, recomputed only as far as `changed` reaches |
| the price of the product picked (onchange) | `@compute(..., editable=True)` | `values` when `changed` names the cell (`lines.0192….product_id`); a save keeps what was typed |
| a rule over the fields (a line sells something) | `@check` | `warnings[]` while drafting; `ChecksFailed` → one field error per failure on save |
| read-only, required, visible — per state | `presentation` | `Meta` hints, and `states` of a preview evaluated for this record (`states.lines.items[i]` per line) |
| the sum at the foot of the lines | `presentation(totals=…)` | `FieldMeta.total` |
| a warning that does not refuse | `warn_form(...)` in a computation | `warnings[]` with `field` and `path` (`["lines", 2, "price"]`) |
| a refusal pointing at a cell | `raise exceptions.InvalidValue("…", ("lines", line.id, "qty"))` | `Problem.errors[].pointer` = `#/values/lines/0192…/qty`, 422 |
| what a removed line becomes | `orphans=` on the relation | nothing: it is the store's rule |

## 2. One Feature answers the whole form

```python
class QueryGetInvoice(Get[Invoice, ResponseInvoice]): ...
class QueryPreviewInvoice(PreviewEntity[Invoice, ResponsePreviewInvoice]): ...
class CommandEditInvoice(Edit[Invoice, ResponseEditInvoice]): ...


class CommandPostInvoice(Edit[Invoice, ResponseEditInvoice]):
    action: EditAction = EditAction.SAVE


@billing.feature([QueryGetInvoice, QueryPreviewInvoice, CommandEditInvoice])
class InvoiceForms(EntityEdits[Invoice]):
    pass


@billing.feature(CommandPostInvoice)
class PostInvoice(EntityEdits[Invoice]):
    def change(self, invoice: Invoice, dto: CommandPostInvoice, /) -> None:
        super().change(invoice, dto)
        invoice.post(self.numbering.take("invoices")[0])
```

| The form asks | The client sends | Answered by |
|---|---|---|
| a new document: its defaults | `QueryPreviewInvoice(values={})` | `values` whole, `states`, `warnings` |
| "I typed this": totals, states, warnings (Odoo's onchange) | `QueryPreviewInvoice(id=…, values={…}, changed=["lines"])` | what moved, nothing stored |
| keep my work, I continue later | `CommandEditInvoice(id=…, values={…}, version=draft.version)` | the same, stored as the caller's draft |
| save | `…, action=EditAction.SAVE, origin_version=…` | the record as saved |
| confirm / post / approve | `CommandPostInvoice(id=…, …)` | the domain's transition, its number taken only now |
| where was I | `QueryGetInvoice(id=…, draft=True)` | the draft, or the stored record |

## 3. The lines travel as the whole list

- **Every line the form holds, in its order.** A line it got back carries its `id`; a new line
  carries the `id` the form minted for it (UUID v7), and is built under it. A line no longer in
  the list is settled by `orphans=`.
- **A line is named by its identity.** `#/values/lines/0192…/qty`, `states.lines.items["0192…"]`,
  `warnings[].path == ["lines", "0192…", …]` and `changed: ["lines.0192….qty"]` all mean the same
  line wherever it moves: one rule marks them all. A line that is a plain value, with no identity,
  is named by its position in the list the form sent.
- **The version always travels.** A whole list without the version it was read at would delete
  a line somebody else added meanwhile; with it, the save is refused (`StaleAggregate`,
  `DraftConflict`) and the client reads again.
- **A line's own commands are the screen's.** Add, remove, undo, reorder are gestures on the
  list the client holds; the wire carries the result.

## 4. What runs where

- `PreviewEntity` runs `apply_values` and the computations — the entity's own code, nothing of the Feature.
  A value that comes **from another aggregate** (the product's price when a product is picked)
  needs a read: override `change` in an `EntityEdits` and let the form save drafts
  (`EditAction.SAVE_DRAFT`), which runs `change` inside `read_only()`, reads allowed, writes
  refused.
- A refusal raised while the values are read stops at the first one: the form gets one error
  per round trip.
- A `before_save` hook runs on save, never in a preview: a rule the form must see while typing
  belongs to a computation that warns; a rule that refuses belongs to the hook or the
  domain method, raising `InvalidValue` with the line's path.

## Mistakes an agent makes

- **Sending a client-minted `id` on a new line.** Refused as an `id` the document does not hold.
- **Saving the lines without the version.** The whole list replaces what is stored.
- **A per-field `onchange` Feature.** `PreviewEntity` + editable computations + `@check` + `warn_form` answer it, for every
  client at once.
- **Computing a total in a hook.** The preview never shows it; it is a computation.
- **Passing an index to `warn_form` from a line.** The framework knows the line's place; the line
  names only its own field.
