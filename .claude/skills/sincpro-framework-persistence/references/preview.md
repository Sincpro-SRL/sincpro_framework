# Recipe: preview a record before saving — derived fields, `assign`, `Preview`

Use it when a form needs the server to answer "what does this record become?" before a save:
totals as lines are typed, a new record's defaults, which fields appear for these values.

## 1. Declare what the entity computes (optional)

```python
from sincpro_framework.ddd import Derivations, Derive, Entity, advise

@dataclass
class Invoice(Entity):
    partner_id: str
    lines: list[InvoiceLine] = field(default_factory=list)
    discount: Decimal = Decimal("0")
    subtotal: Decimal = Decimal("0")
    total: Decimal = Decimal("0")

    def subtotal_of(self) -> Decimal:
        return sum((l.qty * l.price for l in self.lines), Decimal("0"))

    def total_of(self) -> Decimal:
        if self.subtotal and self.discount > self.subtotal * Decimal("0.10"):
            advise("a discount above 10% needs approval", field="discount")
        return self.subtotal - self.discount

    derivations = Derivations["Invoice"](
        lambda i: [
            Derive(i.subtotal, depends=i.lines, by=Invoice.subtotal_of),
            Derive(i.total, depends=[i.subtotal, i.discount], by=Invoice.total_of),
        ]
    )
```

`by` is the entity's own method. Declaration order does not matter: they run in dependency
order. A cycle, a field computed twice or an unknown name is refused when the class is read.
**`save` runs `recompute_whole(record)` before the `before_save` hooks** — the lines first, then
the record — so stored = previewed, in memory and in SQL. **A save never reads**: a
derivation over a relation not held whole (not loaded, cut by a specification, assigned blind)
is skipped and keeps its stored value, and what reads it computes from that. Never read →
silent; cut or blind → a warning names the field and says to read the relation whole (inside
`context()`, or a specification that does not filter it). The held lines are computed either
way. An entity with no derivations is not looked into. `upsert` and `update_all` compute nothing.

## 2. Expose the preview on the entity's reads

```python
from sincpro_framework.ddd import Preview, ResponsePreview, EntityReads

class ResponsePreviewInvoice(ResponsePreview): pass
class QueryPreviewInvoice(Preview[Invoice, ResponsePreviewInvoice]): pass

@billing.feature([QueryGetInvoice, QueryPreviewInvoice])
class InvoiceReads(EntityReads[Invoice]):
    pass
```

| Call | Answer |
|---|---|
| `QueryPreviewInvoice()` | the new-record form: every default, the states, `values` whole |
| `QueryPreviewInvoice(values={...}, changed=["lines"])` | the totals the typed lines make |
| `QueryPreviewInvoice(id=x, values={"discount": "50"}, changed=["discount"])` | `{"total": "83.00"}`, the stored invoice untouched |

`ResponsePreview`: `values` (what differs from what the form sent, JSON), `fields` (each field's
`readonly`/`required`/`visible` evaluated over the new record), `advice`.

## 3. A hand-written edit Command reuses the same pieces

```python
invoice = self.repository.get(dto.id)
assign(invoice, dto.values)          # values read as each field's type, hints refuse nothing
self.repository.save(invoice)        # derivations recomputed, before_save hooks, version check
```

## 4. Lines a form sends

- A new line comes without `id` and without the order's key: `assign` fills `order_id` from
  the SQL mapping (an echoed stale key is replaced). In `MemoryRepository` there is no mapping:
  send the key.
- An edited line comes with its `id` and is updated in place; an unknown `id` is refused.
- A line the form no longer sends is an orphan: declare `Relation.foreign_key(OrderLine,
  identified_by="order_id", orphans=Orphans.DELETE)` (or `DETACH`), or the save is refused.

## Mistakes to avoid

- **Writing inside a preview.** Every store write, numbering `take`, session flush, `text(...)`
  that is not one plain read (a `WITH … UPDATE`, two statements, `FOR UPDATE`) and locking read
  (`for_update`) raises `ProgrammingError` inside `previewing()`. Reads inside a preview do not
  autoflush: changes a unit of work holds pending are not seen, and are written at its commit. Number the document in the Command that saves it;
  show `"/"` before. A bare `ThreadPoolExecutor` does not carry the guard: use `ContextExecutor`.
- **Assigning `id` or `version` from a form.** `assign` refuses framework fields, and a
  preview never answers them. A line may carry its `id`: it is the line the order already
  holds, edited in place, so its row is updated — read the lines first (inside `context()`); an
  `id` the order does not hold is refused. Lines without ids over lines never read replace
  them, exactly as `order.lines = [...]` does — blind, so the save keeps the totals and warns.
- **Computing in a hook instead of a derivation.** A `before_save` hook runs on save only; the
  preview would not show it. A value a form must see comes from `derivations`.
- **A derivation with side effects.** `by` reads the record and answers a value: no repository,
  no event publishing, no remote call. Use `advise(...)` to tell the form something.
- **Mutating a line record in `by`.** The preview works on a copy of every record inside the
  aggregate, so nothing leaks into a store; but a derivation answers a value, it does not
  rewrite the lines.
- **Expecting every relation in a preview.** The stored record is read with
  `DEFAULT_READING` plus every relation its derivations read; a relation neither names
  comes back empty.
- **Expecting a preview's `Decimal` at the column's scale.** Nothing rounds it; a derivation
  that must match `Numeric(12, 2)` quantizes its own result.
- **Previewing by saving and rolling back.** Refused by the guard, and wrong: locks, numbers,
  hooks, events.
