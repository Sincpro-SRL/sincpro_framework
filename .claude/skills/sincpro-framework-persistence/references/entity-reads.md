# Recipe: the reads of one aggregate with `EntityReads`

Use it when a screen, an API or an agent needs to read an aggregate: one record, several by
key, a select that finds by the typed text, or a page by criteria. It reads what the entity
answers in its `DEFAULT_*` class methods; what a caller's criteria names wins.

## 1. The entity answers how it is read (override only what you need)

```python
from dataclasses import dataclass, field
from sincpro_framework.ddd import (
    TEXT, Any, Condition, Criteria, Entity, Operator, Sort, Specification,
)

@dataclass
class Invoice(Entity):
    number: str
    name: str = ""
    customer_id: str | None = None
    customer: "Customer | None" = None
    lines: list["InvoiceLine"] = field(default_factory=list)

    @classmethod
    def DEFAULT_GET_ID(cls) -> str:                 # Get(id="F-001") finds it by number
        return "number"                             # the column must be unique

    @classmethod
    def DEFAULT_READING(cls) -> Specification:      # what one record brings, at any depth
        return Specification.model_validate({
            "number": {}, "name": {},
            "customer": {"specification": {}},      # identity + display
            "lines": {"pagination": {"limit": 300}},  # in InvoiceLine's DEFAULT_ORDER
        })

    @classmethod
    def DEFAULT_ORDER(cls) -> tuple[Sort, ...]:     # lists, and where another entity holds it
        return (Sort(field="number", descending=True),)

    @classmethod
    def DEFAULT_DISPLAY(cls) -> str:                # shown beside the identity
        return "number"

    @classmethod
    def DEFAULT_LITERAL_SEARCH(cls) -> Criteria:    # a template: a where holding TEXT
        return Criteria(where=Any(any=[
            Condition(field="number", operator=Operator.STARTS_WITH, value=TEXT),
            Condition(field="name", operator=Operator.LIKE, value=TEXT),
        ]))
```

Nothing overridden: the key is `id`, a record brings every scalar and no relation, the order
is newest first, the field `name` is the display and a text finds it by containment.

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

No `criteria = Field(default_factory=...)` on a DTO: the entity's `DEFAULT_*` already is the
default, and a DTO default would replace it.

## 3. One Feature answers them

```python
from sincpro_framework.ddd import EntityReads

@billing.feature([QueryGetInvoice, QueryGetManyInvoices, QueryFindInvoices, QueryListInvoices])
class InvoiceReads(EntityReads[Invoice]):
    pass                                            # reads from the dependency "repository"
```

| Call | Answer |
|---|---|
| `bus(QueryGetInvoice(id="F-001"), ResponseInvoice)` | the invoice whose `number` is `F-001`, as `DEFAULT_READING` brings it, or `AggregateNotFound` (404) |
| `bus(QueryGetManyInvoices(ids=["F-1", "F-2"]), ResponseInvoices)` | `invoices` in the order of `ids`, `missing` for absent keys |
| `bus(QueryFindInvoices(text="F-001"), ResponseListInvoices)` | the template filled, 8 records, identity + display; blank text → first 8 in order |
| `bus(QueryListInvoices(), ResponseListInvoices)` | a page, each as `DEFAULT_READING` brings it, in `DEFAULT_ORDER` |

## 4. The caller's criteria wins, part by part

Every DTO takes `criteria`. Each part it **names** (`model_fields_set`) replaces the entity's
default — `specification`, `order`, `pagination`… — and its `where` adds to the read's own
filter. `Criteria()` names nothing: the entity's defaults, exactly.

```python
QueryGetInvoice(id="F-001", criteria=Criteria(specification=Specification({"total": Criteria()})))
#   → identity + total, even if DEFAULT_READING does not name total
QueryListInvoices(criteria=Criteria(order=(Sort(field="name"),)))     # → by name
QueryGetInvoice(id="F-001", criteria=Criteria(where=Condition(field="state", value="open")))
#   → the key AND state = open: a closed F-001 is not found
```

A scope a Feature adds keeps the defaults when merged with `merged_with`, which carries only
what either side named:

```python
def search(self, dto: QueryListInvoices) -> ResponseListInvoices:
    scope = Criteria(where=Condition(field="tenant_id", value=dto.tenant_id))
    return super().search(dto.model_copy(update={"criteria": scope.merged_with(dto.criteria)}))
```

## 5. Extend without leaving the pattern

- Before/after one read: override `get`, `get_many`, `literal_search` or `search`, call `super()`.
- Repository named otherwise: override `reads_from(self) -> Repository` and return it.
- Around every use case: a bus interceptor. Replace it entirely: `replaces=InvoiceReads`.

## 6. A record's history

```python
class ResponseIssueEvents(ResponsePaginatedQuery):
    events: list[ProjectEvent]              # the context's event class

class QueryIssueEvents(DomainEvents[Issue, ResponseIssueEvents]):
    pass                                    # register it on IssueReads with the other reads
```

`QueryIssueEvents(id=...)` answers the issue's events, oldest first, filtered by
`entity_type="Issue"` and the issue's identity together; `names=[...]` (or one name) keeps only
those wire names, an unknown one answering no rows; the caller's criteria adds a filter or
replaces the order. Several records at once: `repository.search(ProjectEvent,
history_of(issue, *runs))`. Never one hand-written "list X events" Feature per aggregate.

## Mistakes to avoid

- **A `DEFAULT_*` as a value, a `@staticmethod` or a field.** Always `@classmethod`; anything
  else is refused when the class is declared.
- **A `where` in `DEFAULT_READING`.** It answers a `Specification` — what of each record,
  never which records; a `Criteria` there is refused.
- **A literal template without `TEXT`, or with an order or a page.** A template is a `where`
  alone that says where the text goes; the read sets the page and the order.
- **A key that is not unique.** `DEFAULT_GET_ID` other than `id` needs a unique column, or
  `describe` refuses the mapped class.
- **Returning the entity from a hand-written read Feature.** A record that crosses the bus is
  a DTO: use a `ResponseRecord`/`ResponseRecords`/`ResponsePaginatedQuery` subclass, so the
  specification cuts it on the wire.
- **One DTO with `id: str | list[str]`.** Use `Get` for one and `GetMany` for many: each keeps
  an exact response and its own «not found».
- **Using `EntityReads` to change the record.** It answers reads. A Command that changes the
  invoice reads it with `repository.get(id, detail=detail_of(Invoice))` and saves it.
- **Generating writes.** A write is a Command with an intent (`CommandConfirmInvoice`), written
  by hand.
