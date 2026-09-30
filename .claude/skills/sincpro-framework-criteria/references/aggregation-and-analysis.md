# Aggregation, grouping and analysis

All aggregation is answered **in SQL over the whole result set**, never over a page. Grouping a
paginated set can never come out of the page.

## Sums, groups, pivots

```python
repository.measures(Invoice, None, total=("sum", "total"))         # {"total": 475}
repository.group_by(Invoice, criteria, by=["state"])              # GROUP BY in SQL, no rows
repository.pivot(Invoice, criteria, rows="state", columns="customer_id")   # four statements, any volume
```

`group_by_levels` with a page asked gives every group the ids of its first page and a cursor.
`where_measures` filters the groups the rows made (it reads the names in `measures` plus `count`); a
name outside them is refused rather than dropped, because a filter that vanished there would answer
groups the caller ruled out.

**Pagination happens inside the group, not over the total.**

## Export and explain

```python
for row in repository.export(Invoice, criteria):     # every page as dicts, one at a time
    ...                                              # a report or CSV without holding the set

repository.explain(Invoice, criteria)                # the Select, what was dropped, statement count
```

## The escape hatch returns to the same envelope

A layer with no escape hatch is a layer someone bypasses:

```python
stmt = repository.statement(Invoice, criteria)       # a real SQLAlchemy Select
stmt = stmt.add_columns(func.row_number().over(...))
page = repository.run(Invoice, stmt, criteria)       # back to the same EntityCollection
```

## Narrowing (multi-tenant)

```python
anas_books = repository.narrowed(Criteria(where=Condition(field="customer_id", value=ana.id)))
```

`narrowed(criteria)` is the same database seen through a filter **nothing can widen**: a tenant, a
branch, a permission. It applies to reads *and* writes.

## Reading a query once — `data_analysis`

`sincpro_framework.data_analysis` holds what a `Criteria` answered as a `DataFrame`, so the same
question is not sent twice. It is a utility: it holds, merges and hands on; the analysis is the
dataframe library's. `pip install sincpro-framework[data-analysis]`.

```python
from sincpro_framework.data_analysis import QueryCache

cache = QueryCache(max_rows=2_000_000)
first = cache.fetch(repository, InvoiceLine, posted)          # one page (limit 500)
more  = cache.fetch(repository, InvoiceLine, posted, pages=2) # reads only the missing page
all_rows   = cache.fetch_all(repository, InvoiceLine, posted) # complete
september  = all_rows.narrow({"field": "posted_at", "operator": ">=", "value": "2026-09-01"})  # no read
```

- A read is held under the repository's **fingerprint** — filter, order, mask, scope — never the
  page. `cache.get(...)` is what is held; `cache.invalidate(target)` lets it go.
- `frame.narrow(where)` answers one more condition without a read, but only on a **complete** frame;
  a frame that stopped at a page refuses (`NotComplete`).
- Hand-off: `frame.to_arrow()` / `to_parquet()` / `to_ipc()` / `to_json()`, or straight into polars,
  pandas, DuckDB (Arrow PyCapsule interface). `DataFrame.from_arrow(table)` comes back.
- On a `Database`, `invalidate_on_commit(database, cache, *aggregates)` lets go of an aggregate's
  reads when a write of it commits (not at the flush, not on rollback).

Full detail: `docs/data_analysis/README.md`. The cache of a bus Query's answer is `QueryCaching`
(`docs/caching/README.md`), a different thing.
