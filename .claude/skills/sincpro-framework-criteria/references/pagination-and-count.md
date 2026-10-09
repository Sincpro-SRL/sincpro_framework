# Pagination and count

## Keyset by cursor (the default)

`Pagination(limit=50, strategy=Cursor())` is what an empty `Criteria()` asks: the first 50 rows in
the model's default order (`-id`, newest first, since ids are UUID v7). `limit` has no ceiling.

`page.cursor` is opaque and typed — it carries datetimes and decimals. Resuming is the same
criteria with the token the page handed back:

```python
page = repository.search(Invoices, big_ones)
next_page = repository.search(Invoices, big_ones.resuming_from(page.cursor))
```

In JSON it is `"pagination": {"limit": 20, "strategy": {"token": "<cursor>"}}`. A cursor belongs to
one ordering over one filter; one minted under another ordering is refused.

**`model_copy(update={"cursor": …})` does nothing**: `cursor` is read through the strategy, so the
page never advances and a loop that walks pages never ends. `resuming_from` exists for this.

A cursor names a **row**, not a position, so a row inserted meanwhile shifts nothing. Two traps
behind the "orderable" rule:

- **A row with `NULL` in any keyset column would be silently omitted** — that is how tuple
  comparison works. Hence ordering by a nullable field raises `InvalidCriteria` when the criteria
  runs; declare the column `nullable=False` if screens order by it.
- **The ordering needs a composite index in that exact order, with the id last.** Without it the
  `ORDER BY` is a full sort and the cursor buys nothing. Nothing checks the index for you.

## Offset — when you must

`Pagination(limit=20, strategy=Offset(rows=40))`, JSON `{"limit": 20, "strategy": {"rows": 40}}`,
skips rows. Its cost grows with the offset and pages repeat or skip rows under concurrent inserts.
Use it for a one-off, non-shared read, or to page groups (`grouping.pagination`), where no stable
key exists.

## Walking every page

`repository.stream(...)` yields **pages** (`EntityCollection`), following the cursors; each page is
fetched when the loop reaches it:

```python
for page in repository.stream(Invoices, big_ones):
    for invoice in page:
        ...                                # each record exactly once, even with inserts mid-walk
```

`repository.fetch_all(Invoices, criteria)` is every page as one complete collection (exact count,
no cursor) — for a set somebody already knows is small. `repository.export(...)` walks the pages as
dicts, one record at a time. Inside a unit of work, `unit.commit()` after each streamed batch keeps
a long job from holding one transaction for the whole run.

## Count: capped by default

```python
counted = repository.count(Invoice)        # Count(value=10000, exact=False) → "more than 10 000"
counted.value, counted.exact
```

| Tier | When | Cost |
|---|---|---|
| free | the first page came back shorter than `limit` → exact, you hold them all | zero extra queries |
| `capped` (default) | `SELECT count(*) FROM (… LIMIT 10001)` → `exact=False` past 10 000 | bounded regardless of table size |
| `exact` | `"count": "exact"` | the full price |
| `none` | `"count": "none"` → `page.count is None` | nothing |

`count.exact is False` already says the set is partial.

## Never aggregate over a page

```python
c = repository.search(Invoices, criteria)   # 20 of 8,412
c.sum_by(lambda i: i.total)                 # raises ContractViolation, naming repository.measures(...)
```

Only `sum_by`, `average_by`, `min_by` and `max_by` called **directly on a page** (a cursor, a
capped count, or fewer items than the count) refuse. `count_where` counts what is held, never what
exists. `grouped`, `filtered`, `partition`, `|`, `&` and `-` work on whatever is held: their result
carries no count, so it is no longer marked partial, and folding it answers about the page only,
with no error. For totals over a query use `repository.measures` / `group_by`, or fold a
`fetch_all` — never a fold over anything derived from `search`.

## Derived collections carry no page

Any filtered/unioned/re-sorted collection returns `cursor=None, count=None`. It is not a page of
anything; carrying the cursor would let someone ask for "the next page" of a set that no longer
exists. The metadata is either true or absent, never wrong — and absent also means the
collection no longer knows it came from a page.
