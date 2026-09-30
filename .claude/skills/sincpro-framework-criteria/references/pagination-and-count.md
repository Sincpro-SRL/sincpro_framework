# Pagination and count

## Keyset by cursor (the default)

`cursor` is opaque and typed — it carries datetimes and decimals. Resuming is the same criteria with
the token the page handed back:

```python
from sincpro_framework.ddd import Cursor, Pagination

next_page = repository.search(
    Invoices,
    big_ones.model_copy(
        update={"pagination": Pagination(limit=2, strategy=Cursor(token=page.cursor))}
    ),
)
```

A cursor names a **row**, not a position, so a row inserted meanwhile shifts nothing.

In JSON it is `"pagination": {"limit": 2, "strategy": {"token": "<cursor>"}}`.

Two traps behind the "orderable" rule:

- **A row with `NULL` in any keyset column is silently omitted.** That is how tuple comparison works.
- **Ordering needs a composite index in that exact order**, with the id last so the key is unique.
  Without it the `ORDER BY` is a full sort and the cursor buys nothing.

Hence: order only by `NOT NULL` columns that are indexed. A nullable column is refused at
declaration rather than losing rows.

## Offset — when you must

`Offset(rows=…)` skips rows. It is expensive (cost grows with the offset) and yields incoherent
pages under concurrent inserts. Prefer the cursor; use `Offset` only for a one-off, non-shared read.

## Walking every page

`repository.stream(...)` continues the criteria from each cursor for you:

```python
for one in repository.stream(Invoices, big_ones):
    ...                                    # each record exactly once
```

`repository.fetch_all(Invoices, criteria)` is every page as one collection (bounded use).

## Count: capped by default

```python
repository.count(Invoice).value            # Count(value=412, exact=False) → "more than"
```

| Tier | When | Cost |
|---|---|---|
| free | fewer rows came back than `limit` → you have them all, exact | zero extra queries |
| capped (default) | `count: capped` — `SELECT count(*) FROM (… LIMIT 10001)` | bounded regardless of table size |
| exact / estimated on demand | `count: exact` | the full price |

`count.exact is False` already says the set is partial.

## Never aggregate over a page

```python
c = repository.search(Invoice, criteria)   # 20 of 8,412
c.sum_by(lambda i: i.total)                # ← the sum of TWENTY, looking like the sum of all
```

Aggregating over a partial collection **raises** and names the engine call that answers correctly
(`repository.measures(...)`). A wrong number is worse than no number.

## Derived collections carry no page

Any filtered/unioned/re-sorted collection returns `cursor=None, count=None`. It is not a page of
anything; carrying the cursor would let someone ask for "the next page" of a set that no longer
exists. The metadata is either true or absent, never wrong.
