# Relations

An aggregate reaches another by naming it in a `Criteria` `specification`. Resolution is batched —
**one call per relation per page**, never per row — and a relation you did not ask for raises rather
than lazily loading.

In the framework repo, `docs/persistence/relations.md` and `tests/orm/every_kind_models.py` (every
kind at once) go deeper; this page is enough to use them.

## The kinds

| Kind | How it resolves | Who writes it |
|---|---|---|
| **Local, mapped FK** — the common case | one statement per node, `WHERE fk IN (page ids)`, cut per parent by a window | inferred from the `ForeignKey` |
| **Local, id list** in a `JsonText` column | one statement per node in the same session | `Relation.id_list(...)` |
| **Many-to-many** | joined through the table in between | `Relation.many_to_many(...)`, because a list cannot say there is a table |
| **Cross-context** | one Command on the other context's bus with every parent key | `Relation.bus(...)` |
| **Any function** | one call of `resolver(keys, criteria)` | `Relation.resolved_by(...)` |

## Inference precedence

1. A column with that name is a **field**. Never a relation.
2. A **declaration wins over inference**.
3. A **foreign key is inferred when unambiguous**: the annotation points at a mapped class and
   exactly one `ForeignKey` ties the two tables (with several, the column named `<attribute>_id`
   decides; with none of those, nothing is inferred — declare it).
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

Defaults, not ceilings: a to-many node without a `pagination` brings 40 per parent; a node that
asks for more gets more, at any depth. A name the model does not have is dropped as
`unknown_field`, a relation nothing declared how to resolve as `not_expandable` — both reported in
`page.dropped`, never raised. A provider that wants a ceiling merges a criteria of its own on the
way in. `repository.explain(...)` says how many calls a criteria will cost before running it.

## Cross-context

`Relation.bus(Review, reviews_bus, CommandSearchReviews, identified_by="book_id")` calls the other
context's bus with `CommandSearchReviews(criteria=…)`, the criteria carrying every parent key. The
other side writes nothing special: a Feature that takes a `Query` and answers
`ResponsePaginatedQuery.of(page, criteria)`. This side imports only that context's Command and the
class it reads the answer as, never its Features or its repository.

Every relation may carry a `scope=Criteria(...)`: the filter and order every reading of it starts
from (`scope=Criteria(order=parse_order("-created_at"))` for "newest first"). What a caller names
in the specification merges on top and can only narrow it.
