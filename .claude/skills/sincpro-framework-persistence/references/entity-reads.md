# Recipe: the reads of one aggregate with `EntityReads`

Use it when a screen, an API or an agent needs to read an aggregate: one record, several by
identity, a select that finds by the typed text, or a page by criteria. It reads what the
entity declared in `presentation` (display, search, order, detail).

## 1. The entity declares how it is read (optional)

```python
from dataclasses import dataclass, field
from sincpro_framework.ddd import Entity, Expand, Match, Presentation, Reference

@dataclass
class Invoice(Entity):
    number: str
    name: str = ""
    customer_id: str | None = None
    customer: "Customer | None" = None
    lines: list["InvoiceLine"] = field(default_factory=list)

    presentation = Presentation["Invoice"](
        display=lambda i: i.number,
        search=lambda i: [Match.equal(i.number), Match.prefix(i.number), Match.contains(i.name)],
        order=lambda i: i.number,                         # Descending(i.posted_at) for desc
        detail=lambda i: [i.number, Reference(i.customer), Expand(i.lines, 300)],
    )
```

Nothing declared: the field `name` is the display, it is searched by containment, and the
order is newest identity first.

## 2. The project names its responses and DTOs

```python
from sincpro_framework.ddd import (
    Get, GetMany, LiteralSearch, ResponsePaginatedQuery, ResponseRecord, ResponseRecords, Search,
)

class ResponseInvoice(ResponseRecord):              # exactly one field of its own
    invoice: Invoice

class ResponseInvoices(ResponseRecords):            # a list + missing, not a page
    invoices: list[Invoice]

class ResponseListInvoices(ResponsePaginatedQuery): # a page
    invoices: list[Invoice]

class QueryGetInvoice(Get[Invoice, ResponseInvoice]): pass
class QueryGetManyInvoices(GetMany[Invoice, ResponseInvoices]): pass
class QueryFindInvoices(LiteralSearch[Invoice, ResponseListInvoices]): pass
class QueryListInvoices(Search[Invoice, ResponseListInvoices]): pass
```

## 3. One Feature answers them

```python
from sincpro_framework.ddd import EntityReads

@billing.feature([QueryGetInvoice, QueryGetManyInvoices, QueryFindInvoices, QueryListInvoices])
class InvoiceReads(EntityReads[Invoice]):
    pass                                            # reads from the dependency "repository"
```

| Call | Answer |
|---|---|
| `bus(QueryGetInvoice(id=x), ResponseInvoice)` | the invoice with its declared detail, or `AggregateNotFound` (404) |
| `bus(QueryGetManyInvoices(ids=[a, b]), ResponseInvoices)` | `invoices` in the order of `ids`, `missing` for absent ids |
| `bus(QueryFindInvoices(text="F-001"), ResponseListInvoices)` | 8 records, identity + display; blank text → first 8 in order |
| `bus(QueryListInvoices(criteria=...), ResponseListInvoices)` | the page asked, in the declared order |

Every DTO also takes `criteria`: on `Get`/`GetMany` its specification only narrows the detail;
on `LiteralSearch` it adds a filter or a page size.

## 4. Extend without leaving the pattern

- Before/after one read: override `get`, `get_many`, `literal_search` or `search`, call `super()`.
- Repository named otherwise: override `reads_from(self) -> Repository` and return it.
- Around every use case: a bus interceptor. Replace it entirely: `replaces=InvoiceReads`.

## Mistakes to avoid

- **Returning the entity from a hand-written read Feature.** A record that crosses the bus is
  a DTO: use a `ResponseRecord`/`ResponseRecords`/`ResponsePaginatedQuery` subclass, so the
  specification cuts it on the wire.
- **One DTO with `id: str | list[str]`.** Use `Get` for one and `GetMany` for many: each keeps
  an exact response and its own «not found».
- **Using `EntityReads` to change the record.** It answers reads. A Command that changes the
  invoice reads it with `repository.get(id, detail=detail_of(Invoice))` and saves it.
- **Generating writes.** A write is a Command with an intent (`CommandConfirmInvoice`), written
  by hand.
- **Naming fields as strings in `presentation`.** `lambda i: i.number`, so pyright checks the
  name and a rename reaches it.
