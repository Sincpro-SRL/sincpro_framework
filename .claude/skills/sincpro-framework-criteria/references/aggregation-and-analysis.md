# Aggregation, grouping and analysis

All aggregation is answered **in SQL over the whole result set**, never over a page. Grouping a
paginated set can never come out of the page.

A measure is written `("sum", "total")` in Python or `{"function": "sum", "field": "total"}` in
JSON. Functions: `sum`, `avg`, `min`, `max`, `count`, `count_distinct`, `percentile` (with
`{"argument": 0.95}`; not on SQLite). Anything else raises `InvalidCriteria`.

## Sums, groups, pivots

```python
repository.measures(Invoice, criteria, total=("sum", "total"), biggest=("max", "total"))
# {"total": 475, "biggest": 120}                       criteria may be None

repository.group_by(Invoice, ["state"], criteria)
# [{"state": "draft", "count": 3}, {"state": "posted", "count": 4}]   counts per bucket, no rows

matrix = repository.pivot(Invoice, ["state"], ["customer_id"], criteria, total=("sum", "total"))
# Pivot(rows, columns, cells, row_margin, column_margin, total) — four statements, any volume
matrix.cell(["posted"], [ana.id]).measures["total"]
```

Signatures: `group_by(target, by, criteria=None)`, `pivot(target, rows, columns, criteria=None,
**measures)`. `by`, `rows` and `columns` are **lists**; a pivot level may be `"posted_at:month"`
(grains `day`, `week`, `month`, `year`). Margins are folded in the database, never added up from
the cells.

## A grouping tree — `group_by_levels`

The `grouping` part of a criteria, answered as `Bucket`s, one statement per level whatever the
row count:

```python
criteria = Criteria.model_validate({
    "where": {"field": "state", "value": "posted"},
    "grouping": {
        "group_by": [{"field": "customer_id"}, {"field": "posted_at", "grain": "month"}],
        "measures": {"total": {"function": "sum", "field": "total"}},
        "where_measures": {"field": "count", "operator": ">", "value": 10},
        "order": [{"field": "total", "descending": True}],
    },
})
for bucket in repository.group_by_levels(Invoice, criteria):
    bucket.value, bucket.count, bucket.measures, bucket.groups      # its children
    repository.search(Invoices, bucket.criteria)                     # opens the bucket's rows
```

- `where_measures` filters the groups the rows made; it reads the names in `measures` plus
  `count`, and a name outside them is refused rather than dropped.
- `grouping.order` orders the groups (by a level, a measure or `count`); `Criteria.order` orders
  rows.
- `grouping.pagination` pages the first level's groups (an `Offset`, no cursor).
- With `Criteria.pagination` asked too, every deepest bucket carries the `ids` of its first page and
  a `cursor` to continue inside that group.
- `search` with both `grouping` and `pagination` answers "the first `limit` rows of every group" in
  one statement — what the other side of a relation resolved through a bus receives.

## Export and explain

```python
for row in repository.export(Invoice, criteria):     # every page as dicts, one at a time
    writer.writerow(row)                             # a report or CSV without holding the set

repository.explain(Invoice, criteria)                # Explained(sql, ordering, dropped, relations, statements)
```

`export(target, criteria, specification)` takes an optional mask for the columns written — a
`Specification({...})`; a raw dict as the third argument raises `AttributeError`. On an
aggregate that declares relations, pass one (or a criteria with a `specification`): unmasked, the
first row touches every relation and raises `RelationNotResolved` outside `context()`.
`explain` runs nothing: what SQL the criteria becomes, what it dropped and how many calls it costs.

## The escape hatch returns to the same envelope

A layer with no escape hatch is a layer someone bypasses:

```python
from sqlalchemy import func

stmt = repository.statement(Invoice, criteria)       # a real SQLAlchemy Select, keyset applied
stmt = stmt.add_columns(func.row_number().over(order_by=Invoice.total))
page = repository.run(Invoices, stmt, criteria)      # back to the same EntityCollection
```

For joins across tables, window functions or bulk statements inside a transaction, use
`unit.session` within `with repository.context() as unit:` (`sincpro-framework-persistence`).

## Narrowing (multi-tenant, permissions)

```python
anas_books = repository.narrowed(Criteria(where=Condition(field="customer_id", value=ana.id)))
```

`narrowed(criteria)` is the same database seen through a filter **nothing can widen**: every
reading ANDs it, every write outside it raises `ContractViolation`, and an aggregate that cannot
answer the scope is refused rather than read wide. Narrowing again accumulates. Prefer it over
merging a tenant condition into a client's criteria, where a typo would be dropped.

## Reading a query once — `data_analysis`

`sincpro_framework.data_layer.data_analysis` holds what a `Criteria` answered as a `DataFrame` (Arrow), so
the same question is not sent twice. Extra: `pip install sincpro-framework[data-analysis]`.
Full usage is `sincpro-framework-analytics`.

```python
from sincpro_framework.data_layer.data_analysis import QueryCache

cache = QueryCache(max_rows=2_000_000)
first = cache.fetch(repository, InvoiceLine, posted)          # one page
more  = cache.fetch(repository, InvoiceLine, posted, pages=2) # reads only the missing page
whole = cache.fetch_all(repository, InvoiceLine, posted)      # complete
september = whole.narrow({"field": "posted_at", "operator": ">=", "value": "2026-09-01"})  # no read
```

- A read is held under the repository's **fingerprint** — filter, order, mask, scope — never the
  page. `cache.get(...)` is what is held; `cache.invalidate(target)` lets it go.
- `frame.narrow(where)` answers one more condition without a read, but only on a **complete**
  frame; a frame that stopped at a page raises `NotComplete`.
- Hand-off: `frame.to_arrow()` / `to_parquet()` / `to_ipc()` / `to_json()`, or straight into
  polars, pandas, DuckDB. `DataFrame.from_arrow(table)` comes back.
- `invalidate_on_commit(database, cache, *aggregates)` (from `sincpro_framework.data_layer.orm`) lets go of an
  aggregate's reads when a write of it commits (not at the flush, not on rollback).

The cache of a bus Query's answer is `QueryCaching` (`sincpro-framework-caching`), a different thing.
