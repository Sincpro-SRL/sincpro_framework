# Preview: what a record becomes before it is saved

A form asks the server "if I change this, what does the record become?" — the totals of an
invoice when a line is typed, the defaults of a new one, the fields that appear when the payment
is by card — and nothing may be stored while it asks. Three pieces answer it, each optional and
usable alone: derived fields, `assign`, and the preview itself. None is needed to save a record.

## Derived fields

```python
from sincpro_framework.ddd import Derivations, Derive, Entity, advise


@dataclass
class InvoiceLine:
    product: str
    qty: int
    price: Decimal


@dataclass
class Invoice(Entity):
    partner_id: str
    lines: list[InvoiceLine] = field(default_factory=list)
    discount: Decimal = Decimal("0")
    subtotal: Decimal = Decimal("0")
    total: Decimal = Decimal("0")

    def subtotal_of(self) -> Decimal:
        return sum((line.qty * line.price for line in self.lines), Decimal("0"))

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

The computation is the entity's own method; `derivations` only says which field it computes
and what it reads. The lambda answers one `Derive` alone or a list of them, and `depends` is one
field alone or a list. The class is read once: the derivations are put in the order their
dependencies need, and a cycle, a field computed twice, a field computed from itself or a name
that is not a field is refused there, naming it.

| Call | What it does |
|---|---|
| `recompute(invoice)` | every derivation, in order; answers the fields whose value moved |
| `recompute(invoice, ["discount"])` | only what the change reaches: `total`, not `subtotal` |
| `repository.save(invoice)` | runs `recompute_whole(invoice)` before the `before_save` hooks |

**A save computes them too**, so what a preview showed is what is stored and the hooks see the
computed values. It computes the records the aggregate owns first — a line's `amount`
before the total that sums it — the same in memory and in a database. It looks only into the
relations its derivations read, and the lists of records that declare derivations of their
own; never into another aggregate it points at. An entity with no `derivations` is saved
exactly as before: nothing extra is read.

**A save never reads, and never computes over a relation it does not hold whole.** A
derivation that reads a relation that was not loaded, was cut by a specification (filtered or
paged), or was assigned without a whole reading behind it is skipped and keeps its stored value;
a derivation that reads that value computes from it. An order read without its lines, given a
discount and saved, keeps its stored subtotal and stores the total as that subtotal less the
discount — never a subtotal over the lines a client happened to see. The records held are
computed either way: a line edited in a cut set gets its own amount.

A relation never read cannot have changed, so that skip is silent. One held in part — cut by a
specification, or assigned with no whole reading behind it (blind) — may have, so the save keeps
the stored value **and logs a warning** naming the derived field, the relation and the way out:
read it whole, inside `context()` or with a specification that does not filter or page it. Not
a refusal: the record is written, and the developer is told the total may not match its lines.
`upsert` and `update_all`
compute nothing — they are the writes that skip the aggregate's rules — so a value they change
leaves its derived fields as they were until the next `save`.

A computed `Decimal` is not rounded to the scale of the column that stores it: the preview can
answer `18.995` where a `Numeric(12, 2)` keeps `19.00`. A derivation that must match the stored
scale quantizes its own result.

## `assign`: a form's values on a record

```python
changed = assign(invoice, {"discount": "12.50", "lines": [{"product": "x", "qty": "2", "price": "9.90"}]})
# ("discount", "lines") — discount is Decimal("12.50"), lines are InvoiceLine records
```

Each value is read as its field's type, so the JSON a form sends becomes the record's own
values. A name that is not a field, or a value the type cannot read, is refused naming it. A
form hint refuses nothing: `assign(invoice, {"number": "F-9"})` sets a field the form shows
read-only — a hint is the client's default, never a rule. A hand-written edit Command uses it
the same way: `assign`, then `save`.

### Lines: new, edited, dropped

A to-many the entity owns (an order's lines) travels as a list of mappings:

| The form sends | `assign` does |
|---|---|
| a line without `id` | builds a new one; the key pointing back at the order (`order_id`) is filled from the relation — the form never sends it, and one it echoes from an earlier preview is replaced |
| a line with the `id` of one the order holds | edits that line in place, so a save updates its row |
| an `id` the order does not hold | refused, naming it |
| no longer a line the order holds | what the relation declares: `Orphans.DELETE` deletes it, `Orphans.DETACH` sets its key to NULL, nothing declared refuses the save |

```python
map_aggregates(books, tables, relations={
    Order: {"lines": Relation.foreign_key(OrderLine, identified_by="order_id", orphans=Orphans.DELETE)},
})

assign(order, {"lines": [{"id": kept.id, "qty": 5}, {"product": "regla", "qty": 1, "price": "4"}]})
repository.save(order)          # one line updated, one inserted, the dropped one deleted
```

The key is filled from the mapping, so it needs the SQL adapter. `MemoryRepository` has no
mapping: there a line is built from what the mapping gives, its key included.

## The preview

```python
class ResponsePreviewInvoice(ResponsePreview):
    pass


class QueryPreviewInvoice(Preview[Invoice, ResponsePreviewInvoice]):
    pass


@billing.feature([QueryGetInvoice, QueryPreviewInvoice])
class InvoiceReads(EntityReads[Invoice]):
    pass


bus(QueryPreviewInvoice(id=invoice_id, values={"discount": "50"}, changed=["discount"]),
    ResponsePreviewInvoice)
```

| `ResponsePreview` | What it holds |
|---|---|
| `values` | every value that differs from what the form sent, derived fields included, as JSON — `{"total": "83.00"}` |
| `fields` | each field's form hints evaluated over the new record: `{"card_reference": {"readonly": false, "required": true, "visible": true}}` |
| `advice` | what the domain said through `advise(...)`, field by field |

1. With `id` (the record's `DEFAULT_GET_ID`), the preview starts from the stored record,
   read as its `DEFAULT_READING` brings it (so a to-many it names comes with it), and copied:
   the record a store keeps is never touched.
   With no `id`, it starts from a new record holding the class defaults.
2. `assign` puts the form's values on it; `recompute` runs what `changed` reaches — every
   derivation when nothing is named.
3. The events the domain recorded are dropped.
4. With no `id` and nothing changed, `values` is the whole new record: the form a new record
   starts from (Odoo's `default_get`).

`fields` is the same hint `Meta` publishes, already evaluated for this record — what a client
with no Criteria evaluator (a native app, an agent) shows.

## Nothing is written

```python
with previewing() as advice:
    repository.save(invoice)        # WriteInPreview: save inside a preview
```

Everything above runs inside `previewing()`. While it runs, every write door of every store —
`save`, `remove`, `archive`, `upsert`, `update_all`, `remove_all` —, every numbering `take`, and
every flush or writing statement of a database session — `record_changes`, a unit of work
committing what it tracks (`Writes.CHANGED`), a record added to `repository.session` by hand, a
`text(...)` statement that is not a single read — and every read that locks rows
(`for_update`, `FOR UPDATE`/`FOR SHARE` in text) raise `WriteInPreview`, naming the call. A
text statement passes only when it is one statement starting with `SELECT`, or with `WITH` and
no `INSERT`/`UPDATE`/`DELETE`/`MERGE` anywhere in it, comments and opening parentheses aside:
conservative on purpose, so a rare read that trips it is refused inside a preview, never a write
let through. A preview that wrote would leave a record, a spent
number or a lock behind a form, silently; a loud refusal is the one place one belongs. A domain
method that numbers a document belongs to the Command that saves it; until then the form shows
`"/"`.

The guard is a context variable. Outside it: a statement sent on a raw connection, and a
thread started without the framework's context (`ContextExecutor` carries it; a bare
`ThreadPoolExecutor` does not).

A preview inside a unit of work that holds changes not yet flushed (`Writes.CHANGED`) reads
without flushing them: its reads see what is stored, not those pending changes, which the unit
of work writes at its own commit once the preview is over.

`advise(message, field=None)` is how domain code speaks to a form without refusing: inside a
preview the advice is collected and travels back, outside one it does nothing. A preview inside
another hands its advice to the outer one too.

A stored record is previewed from a copy, read as its `DEFAULT_READING` brings it and with every
relation its derivations read, so a total over lines the reading does not name still sums them. Values are
compared as values: `0` and `0.00` are one amount, not a change.

## What is left out

- **Saving and rolling back** to preview: refused by the guard. It takes locks, spends numbers
  and fires hooks, events and mail.
- **An `@onchange` method per field**: derivations and advice answer the same questions without
  an imperative loop.
- **Line commands** for a to-many field: a changed list comes back whole.
- **Drafts** kept between requests: [Drafts](drafts.md).
