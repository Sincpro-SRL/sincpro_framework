---
name: sincpro-framework-criteria
description: Query with sincpro_framework — write a Criteria (filters, ordering, keyset or offset pagination, count, specification), run it on the Repository, paginate page by page, aggregate with measures/group_by/pivot/export, and resolve relations. Use whenever the task lists, filters, sorts, paginates, counts or aggregates records, or builds a screen/endpoint that reads them, in a Sincpro Python service.
---

# sincpro-framework-criteria

`Criteria` is the one object that crosses a boundary. A screen sends it, a saved reading is one,
another bounded context receives one when a relation is resolved through its bus, and a Feature
hands it to the repository. It is JSON, it is typed, and every part of it is optional.

Full depth: `docs/persistence/criteria.md`, `specification.md`, `relations.md`.

## The one object

```python
from sincpro_framework.ddd import Condition, Criteria, Operator, Sort

big_ones = Criteria.model_validate({
    "where": {"field": "total", "operator": ">=", "value": 75},
    "order": [{"field": "total", "descending": True}],
    "pagination": {"limit": 2},
})
page = repository.search(Invoices, big_ones)

page.items          # the records
page.count.value    # how many exist (capped by default)
page.cursor         # the next page's token, or None
page.dropped        # conditions the model could not answer, with a reason
page.meta           # every field and relation, with types and operators
```

## The rules that matter

- **One door for reads.** All reads go through the repository with a `Criteria`. That is what makes
  a cross-cutting concern — access masks, tracing, caching — one file, not an audit of endpoints.
- **Pagination is keyset by cursor by default.** A cursor names a row, not a position, so a row
  inserted meanwhile shifts nothing. `Offset(rows=…)` counts rows instead; it is expensive and
  incoherent under concurrent inserts. Do not use `OFFSET`.
- **Ordering an arbitrary column is refused**, not just discouraged: a keyset over a nullable column
  silently drops rows, and without a composite index it is a full sort. `order` fields must be
  `NOT NULL` and indexed.
- **A count is capped by default** (`count: capped`, stops at 10 000 and says so via
  `count.exact is False`). Free when the first page came back short. Never aggregate over a partial
  collection — it raises.
- **`dropped`, never a 400.** A field the model does not have, an operator its type does not take, a
  value that will not read: each is removed and reported, and the request still runs. A dropped
  filter always *widens* the result, so telling the client is enough. A field in `never_filter`
  fails closed (422).
- **A specification is a mask**, and it can only take away — that is what makes it safe as a
  permission.
- **`Meta` is reflexive.** The answer says what to ask next: fields, types, operators, choices,
  labels, relations. A client never carries a schema of its own.

## References

- [references/criteria-grammar.md](references/criteria-grammar.md) — `where`/`all`/`any`/`negate`, operators by type, merging
- [references/pagination-and-count.md](references/pagination-and-count.md) — cursor vs offset, count tiers, `stream`
- [references/aggregation-and-analysis.md](references/aggregation-and-analysis.md) — `measures`, `group_by`, `pivot`, `export`, `narrowed`, data analysis

## Related

- The aggregate, table and repository: `sincpro-framework-persistence`
- Reading a query once page by page, handed to pandas/polars/DuckDB: `docs/data_analysis/README.md`
