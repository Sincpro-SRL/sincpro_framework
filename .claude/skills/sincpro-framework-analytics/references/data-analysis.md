# Data analysis

Depth: `docs/data_analysis/README.md`, PRD_09. Every block there runs as a test.
`pip install sincpro-framework[data-analysis]` (pyarrow). The core needs nothing.

`sincpro_framework.data_analysis` holds what a `Criteria` answered as a `DataFrame`, so the same
question is not sent to the database twice.

## The rows and the cache

Any `Repository` works. A `QueryCache` keeps a read under the repository's **fingerprint** of it —
the filter, the order, the mask and the scope a repository reads under, never the page. The same
question with more pages reads only the pages not held; asking again reads nothing.

```python
from sincpro_framework.data_analysis import QueryCache

cache = QueryCache(max_rows=2_000_000)

first = cache.fetch(repository, InvoiceLine, posted)            # one page (limit 500)
more  = cache.fetch(repository, InvoiceLine, posted, pages=2)   # reads only the missing page
again = cache.fetch(repository, InvoiceLine, posted, pages=2)   # reads nothing
```

A frame has a column per field of the aggregate (the `Entity` fields included), or the ones the
`Criteria`'s specification keeps. Pages are read with the keyset cursor and without a count, and
merged by `id`, so a row that moved between two pages is held once.

## A narrower filter, with no read

`fetch_all` reads the pages not held yet, so the frame holds every row of its filter. A **complete**
frame answers the filter it holds and one more condition by itself:

```python
from sincpro_framework.data_analysis import NotComplete

sales = cache.fetch_all(repository, InvoiceLine, posted)
september = sales.narrow({"field": "posted_at", "operator": ">=", "value": "2026-09-01"})
sal = september.narrow({"field": "journal", "operator": "=", "value": "SAL"})
```

`where` is written as in a `Criteria` (a condition, or `{"all": …}`, `{"any": …}`, `{"negate": …}`).
A field the frame has no column for is refused, and so is a value not of the column's type. A frame
that stopped at a page refuses (`NotComplete`) — from it there is no telling which rows are missing;
read the narrower filter as its own query instead.

## Not refreshed on its own

```python
cache.invalidate(InvoiceLine)          # what a commit does for you:
frames = invalidate_on_commit(database, QueryCache(), Payment)
```

`invalidate_on_commit` (from `sincpro_framework.orm`) notes what a flush writes and lets go of each
aggregate's reads when that write **commits** — not at the flush, not on a rollback. A cache is per
process; an aggregate another process writes is not one to hold here.

## Handed on

A frame sorts and selects; everything else is for a dataframe library. It speaks the Arrow PyCapsule
interface, so polars, pandas, DuckDB and pyarrow take it as-is, decimals exact.

```python
import duckdb, polars
by_journal = polars.DataFrame(sales).group_by("journal").agg(polars.col("amount").sum())
totals = duckdb.from_arrow(sales.to_arrow()).aggregate("journal, sum(amount) AS total").fetchall()
in_pandas = sales.to_arrow().to_pandas()
```

To a client, a frame travels as Parquet or as an Arrow IPC stream
(`application/vnd.apache.arrow.stream`). `to_json` answers by column, decimals as text, and
`version` is the frame's content as a key (a strong ETag).

## Reference

| | |
|---|---|
| `QueryCache(max_rows=None)` | reads held by fingerprint; past `max_rows`, least-recently-used let go |
| `cache.fetch(repository, target, criteria, pages=1)` / `fetch_all(...)` | at least `pages` pages / every row |
| `cache.get(...)` / `cache.invalidate(target=None)` | what is held / let go |
| `frame.narrow(where)` / `append(page)` / `sort(keys)` / `select(columns)` | in-process operations |
| `frame.to_arrow()` / `to_parquet()` / `to_ipc()` / `to_json()` | hand off |
| `DataFrame.from_arrow(table, key="id")` | a `pyarrow.Table` back as a frame |
| `invalidate_on_commit(database, cache, *aggregates)` | `sincpro_framework.orm` |
| `repository.fingerprint(target, criteria)` | the key of a read |

`QueryCaching` (`docs/caching/README.md`) is a different thing: it keeps a **bus Query's answer**
across replicas.
