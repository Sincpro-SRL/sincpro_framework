# Relations

An aggregate reaches another by naming it in a `Criteria` `specification`. Resolution is batched —
**one call per relation per page**, never per row — and a relation you did not ask for raises rather
than lazily loading.

Full depth: `docs/persistence/relations.md`. `tests/orm/every_kind_models.py` is the executable
version (every kind at once).

## The kinds

| Kind | How it resolves | Who writes it |
|---|---|---|
| **Local, mapped FK** — the common case | `selectinload()`, inferred from the FK | the ORM |
| **Local, id list** in a JSON column | batch in the same session | us, a little |
| **Many-to-many** | joined through the table in between | declared, because a list cannot say there is a table |
| **Cross-context** | one command on the other context's bus with the full id list | us |
| **Any function** | a resolver callable | us |

## Inference precedence

1. A column with that name is a **field**. Never a relation.
2. A **declaration wins over inference**.
3. A **foreign key is inferred when unambiguous**: the annotation points at a mapped class and
   exactly one `ForeignKey` ties the two tables.
4. A pointer nothing identifies is **published, not expandable** (`identified_by = None`; asking
   drops with `not_expandable`).
5. Everything else is ignored.

Cardinality comes from the annotation: `list[Run]` is many, `Run | None` is one. Only `many2many`
is a declared type.

## Declaring what the tables cannot say

```python
from sincpro_framework.orm import Relation

map_aggregates(registry, {Author: author_table, Tag: tag_table, Work: work_table},
    relations={Work: {
        "tags":      Relation.many_to_many(Tag, through=work_tag, this_key="work_id", related_key="tag_id"),
        "sources":   Relation.id_list(Work, identified_by="source_ids"),
        "reviews":   Relation.bus(Review, reviews_bus, CommandSearchReviews, identified_by="book_id"),
        "publisher": Relation.resolved_by(Publisher, identified_by="publisher_id", resolver=fetch_publishers),
    }})
```

## Asking for it

```python
with_customer = Criteria.model_validate({
    "order": [{"field": "number"}],
    "specification": {"number": {}, "customer": {"specification": {"name": {}}}},
})
invoices = repository.search(Invoices, with_customer).items
invoices[0].customer.name

customers_with_invoices = Criteria.model_validate({
    "where": {"field": "name", "value": "Ana"},
    "specification": {"invoices": {"order": [{"field": "number"}], "pagination": {"limit": 2}}},
})
```

Each node is itself a `Criteria`, so it filters, orders and pages independently. `specification` is
a mask: it can only take away, which is what makes it safe as a permission.

## Lazy inside a unit of work, refused outside

```python
with self.repository.context() as repository:
    entry = repository.get(Entry, entry_id)
    entry.lines                    # resolves whole on first touch
    entry.lines[0].account.name    # a second hop, lazily

page = self.repository.search(Entries)
page[0].lines                      # RelationNotResolved: name it in the specification
```

The rule is the same one that made the algorithm: **no hidden N+1**. A page a client cut with the
specification gives way to the whole when touched inside a context.

## What it costs

| Read | Statements or calls |
|---|---|
| a page | 1, plus 1 for the count unless the page came back short |
| a page with `k` named relations | `+ k` |
| a page with a relation and, inside it, another | `+ 2` |
| a page with a relation from another context | `+ 1` here, `+ 1` command on the other |

Bound: max depth 3, a default and maximum limit per relation, undeclared path rejected before
touching the database.

## Cross-context

`Relation.bus(...)` is a `BusResolver`. The other side writes nothing special: a Feature that takes a
`Query` and answers `ResponsePaginatedQuery.of`. `Relation.remote`/`bus` name the target by string,
never an imported class — that is what keeps contexts from importing each other.
