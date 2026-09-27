# Data analysis — read a query once

`sincpro_framework.data_analysis` holds what a `Criteria` answered as a `DataFrame`, so the same
question is never sent to the database twice: the next page continues what is held, a narrower
filter is answered from a complete read, and the rows go on — as Parquet or an Arrow stream to a
client, or straight into pandas, polars or DuckDB for the computing. It is a utility, not an
engine: it holds, merges and hands on; the analysis is theirs.

The core needs nothing. Parquet, Arrow IPC and the hand-off to dataframe libraries need pyarrow:

```bash
pip install sincpro-framework[data-analysis]
```

Every block on this page runs, in order, in `tests/docs/test_persistence_guide.py`.

## The rows

Any `Repository` works — the SQLAlchemy one, the memory one, one of yours. Here, invoice lines in
memory, counting how many times they were asked for:

```python
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sincpro_framework.ddd import Criteria, Entity, MemoryRepository


@dataclass
class InvoiceLine(Entity):
    journal: str = ""
    state: str = ""
    posted_at: date = date(2026, 1, 1)
    amount: Decimal = Decimal("0")


class CountedLines(MemoryRepository):
    def __init__(self) -> None:
        super().__init__()
        self.reads = 0

    def search(self, target, criteria=None, for_update=False, skip_locked=False):
        self.reads += 1
        return super().search(target, criteria, for_update, skip_locked)


repository = CountedLines()
for n in range(2_500):
    repository.save(
        InvoiceLine(
            id=f"L{n:05d}",
            journal=["SAL", "PUR", "BNK"][n % 3],
            state="posted" if n % 4 else "draft",
            posted_at=date(2026, 1 + n % 9, 1 + n % 28),
            amount=Decimal(n % 997) / 10,
        )
    )

posted = Criteria.model_validate(
    {
        "where": {"field": "state", "operator": "=", "value": "posted"},
        "order": [{"field": "id"}],
        "pagination": {"limit": 500},
    }
)
```

## Page after page, each read once

A `QueryCache` keeps a read under the repository's fingerprint of it — the filter, the order, the
mask and the scope a repository reads under, never the page. The same question with more pages
reads only the pages not held — `pages` is how many the frame holds after the call, not how many
more to read; asking again reads nothing:

```python
from sincpro_framework.data_analysis import QueryCache

cache = QueryCache(max_rows=2_000_000)

first = cache.fetch(repository, InvoiceLine, posted)
assert (len(first), first.complete, repository.reads) == (500, False, 1)

more = cache.fetch(repository, InvoiceLine, posted, pages=2)
assert (len(more), repository.reads) == (1_000, 2)

again = cache.fetch(repository, InvoiceLine, posted, pages=2)
assert again is more and repository.reads == 2
```

A frame has a column per field of the aggregate — `id`, `created_at`, `updated_at` and `version`
of `Entity` included — or the ones the `Criteria`'s specification keeps. Pages are read with the repository's keyset cursor and without a count, and merged by `id`, so
a row that moved between two pages is held once. `"1000"` and `1000`, `"10.0"` and `"10.00"`, or
the parts of an `all` in another order are the same question and share what is held.

## A narrower filter, with no read

`fetch_all` reads the pages not held yet, so the frame holds every row of its filter. A complete
frame answers the filter it holds and one more condition by itself — values written as JSON are
read as each column's type:

```python
from sincpro_framework.data_analysis import NotComplete

sales = cache.fetch_all(repository, InvoiceLine, posted)
assert (len(sales), sales.complete, repository.reads) == (1_875, True, 4)

september = sales.narrow({"field": "posted_at", "operator": ">=", "value": "2026-09-01"})
sal = september.narrow({"field": "journal", "operator": "=", "value": "SAL"})
assert repository.reads == 4
assert all(line["journal"] == "SAL" for line in sal.rows())

try:
    first.narrow({"field": "journal", "operator": "=", "value": "SAL"})
except NotComplete:
    pass  # a frame that stopped at a page cannot tell which rows it is missing
```

`where` is written as in a `Criteria`: a condition, or `{"all": [...]}`, `{"any": [...]}` and
`{"negate": ...}` of them. A field the frame has no column for is refused, and so is a value that is
not of the column's type:

```python
recent_sal_or_bnk = sales.narrow(
    {
        "all": [
            {"any": [{"field": "journal", "operator": "=", "value": "SAL"}, {"field": "journal", "operator": "=", "value": "BNK"}]},
            {"negate": {"field": "posted_at", "operator": "<", "value": "2026-06-01"}},
        ]
    }
)
assert set(recent_sal_or_bnk.column("journal")) == {"SAL", "BNK"}

try:
    sales.narrow({"field": "jurnal", "operator": "=", "value": "SAL"})
except KeyError as error:
    assert "jurnal is not a column" in str(error)
```

A frame that stopped at a page refuses: from it there is no telling which rows are missing. Read
the narrower filter as its own query instead — `cache.fetch` with that `Criteria`.

What is held is not refreshed on its own. The process that writes knows when it matters:

```python
cache.invalidate(InvoiceLine)
assert cache.get(repository, InvoiceLine, posted) is None
sales = cache.fetch_all(repository, InvoiceLine, posted)
```

On a `Database`, `invalidate_on_commit` does it: what a flush writes is noted, and the reads of
each aggregate given are let go when that write commits — not at the flush, where a read in
between would hold the rows the commit is about to change, and not on a rollback, which changed
nothing. A cache is per process: an aggregate another process writes is not one to hold here.

```python
from sqlalchemy import Column, Integer
from sqlalchemy.orm import registry

from sincpro_framework.orm import Database, Repository, entity_table, invalidate_on_commit, map_aggregates


@dataclass
class Payment(Entity):
    cents: int = 0


mapping = registry()
map_aggregates(mapping, {Payment: entity_table("payment", mapping.metadata, Column("cents", Integer))})
database = Database("sqlite:///payments.sqlite3")
mapping.metadata.create_all(database.engine)
payments = Repository(database)
every_payment = Criteria.model_validate({"order": [{"field": "id"}]})

frames = invalidate_on_commit(database, QueryCache(), Payment)
none_yet = frames.fetch_all(payments, Payment, every_payment)
assert len(none_yet) == 0 and "cents" in none_yet.columns     # no rows, still its columns

payments.save(Payment(cents=1_000))
assert frames.get(payments, Payment, every_payment) is None
assert len(frames.fetch_all(payments, Payment, every_payment)) == 1
```

A read that answers no row still has its columns — the identity and what the specification keeps,
or every field the model describes — typed by the model, so it joins, and goes to Arrow and
Parquet, as one with rows would.

## Handed on

A frame sorts and selects; everything else is for a dataframe library. It speaks the Arrow
PyCapsule interface, so polars, pandas, DuckDB and pyarrow take it as it is, decimals exact:

```python
import duckdb
import polars

top = sales.sort([("amount", "desc"), ("id", "asc")]).select(["id", "journal", "amount"])
assert top.column("amount")[0] == max(sales.column("amount"))

by_journal = polars.DataFrame(sales).group_by("journal").agg(polars.col("amount").sum()).sort("journal")
assert by_journal["amount"].sum() == sum(sales.column("amount"))

totals = duckdb.from_arrow(sales.to_arrow()).aggregate("journal, sum(amount) AS total").fetchall()
assert sum(total for _, total in totals) == sum(sales.column("amount"))

in_pandas = sales.to_arrow().to_pandas()
assert len(in_pandas) == len(sales)
```

What DuckDB or polars answer as Arrow comes back as a frame with `DataFrame.from_arrow`, its types
read from the schema — so an answer with no rows keeps its columns:

```python
from sincpro_framework.data_analysis import DataFrame

journals = DataFrame.from_arrow(
    duckdb.from_arrow(sales.to_arrow()).aggregate("journal, sum(amount) AS total").arrow().read_all()
)
assert set(journals.column("journal")) == {"SAL", "PUR", "BNK"}
assert dict(zip(journals.columns, journals.types))["total"] == "decimal"
```

To a client, a frame travels as Parquet — to download or keep — or as an Arrow IPC stream,
`application/vnd.apache.arrow.stream`, for one that appends page after page. Both are a
fraction of the JSON. `to_json` answers by column, with decimals as text and `types` saying so,
and `version` is the frame's content as a key — the same rows, the same version, a strong ETag.
In Arrow, a decimal column's scale is the largest among its values, an aware datetime is kept in
UTC, and a column with no value is null:

```python
import io

import pyarrow.ipc
import pyarrow.parquet

parquet = sales.to_parquet()
assert pyarrow.parquet.read_table(io.BytesIO(parquet)).num_rows == len(sales)
assert pyarrow.ipc.open_stream(sales.to_ipc()).read_all().num_rows == len(sales)
stream = sales.to_ipc()
body = sales.to_json()

assert len(parquet) < len(str(body))
assert body["types"][body["columns"].index("amount")] == "decimal"
assert sales.version == cache.fetch_all(repository, InvoiceLine, posted).version
```

## Reference

| | |
|---|---|
| `QueryCache(max_rows=None)` | reads held by fingerprint; past `max_rows`, the ones used longest ago are let go |
| `cache.fetch(repository, target, criteria, pages=1)` | at least `pages` pages, only the missing ones read |
| `cache.fetch_all(repository, target, criteria)` | every row, the frame complete |
| `cache.get(...)` / `cache.invalidate(target=None)` | what is held, or nothing / let go of it |
| `frame.narrow(where)` | the rows that also answer `where`; `NotComplete` on a frame that stopped at a page |
| `frame.append(page)` | the next page merged by `key`; `SchemaMismatch` for other columns |
| `frame.sort(keys)` / `frame.select(columns)` | ordered, `null` last / some columns |
| `frame.to_arrow()` / `to_parquet()` / `to_ipc()` / `to_json()` | handed on |
| `DataFrame.from_arrow(table, key="id")` | a `pyarrow.Table` as a frame, typed by its schema |
| `invalidate_on_commit(database, cache, *aggregates)` | `sincpro_framework.orm`: the reads of an aggregate let go when a write of it commits |
| `repository.fingerprint(target, criteria)` | the key of a read — pagination left out, scope put in |
