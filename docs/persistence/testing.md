# The real-world suite: a ledger, three contexts, and the numbers

`tests/realworld/` runs the persistence and events layers the way a service would use them,
over a populated accounting ledger, at a volume the run decides. The unit suites under
`tests/ddd`, `tests/orm` and `tests/events` prove each piece; this one proves they hold together
under load, concurrency and a process boundary.

```
make test              the unit suites; everything marked `realworld` is left out
make test-realworld    the ledger cases at 2 000 entries (~6 000 lines), a few seconds
make test-stress       the stress cases at 25 000 entries (~75 000 lines), timed
```

| Variable | Default | What it changes |
|---|---|---|
| `SINCPRO_REALWORLD_ENTRIES` | `2000`, `25000` under `make test-stress` | how many entries the population writes |
| `DATABASE_URL` | a SQLite file under the pytest tmp dir | any SQLAlchemy URL; the Postgres-only cases stop skipping |

## Testing a Feature without a database

`MemoryRepository` is the dependency a unit test hands a bus instead of the adapter:

```python
ledger = MemoryRepository(*accounts)
bus.add_dependency("repository", ledger)
```

It answers the reads, the short readings, the folds, the groups and the writes, with the same
version check and the same archive rule. It filters with `matches`, the evaluator the SQL
translator is checked against, so the two agree by construction. What it does not have —
relations, units of work, date grains — it says rather than guesses, and that is where the
suite below takes over.

## What is in the directory

| Module | Role |
|---|---|
| `ledger.py` | the domain: `Journal`, `Account`, `Partner`, `Entry`, `Line`; `EntryPosted`, `BalanceUpdated`; the tables and one `map_aggregates` |
| `population.py` | the deterministic population, in batches of 500 inside `context()`; answers a `Census` |
| `contexts.py` | three `UseFramework` instances: ledger (`PostEntry`), reporting (`UpdateBalances` on `EntryPosted`), notifications (on `BalanceUpdated`) |
| `databases.py` | how a database is opened here and in a worker: WAL and a busy timeout on SQLite, nothing on Postgres |
| `workers.py` | what runs in another process: a second writer, and a subscriber that builds its own database and buses |
| `conftest.py` | one populated database per session, fresh contexts per test, `new_draft` for tests that write, `timed` for the summary |

The invariant every test can lean on is double entry: over posted lines the debits equal the
credits, and every account's balance equals the sum of what was posted to it. A test that
writes creates its own entries, dated after the population, so the reading tests compare
against the census with `POPULATION` and are never disturbed.

## What is proven

- **Reads at volume.** A keyset walk visits every line exactly once in order; the capped count
  says when it stopped counting; every operator agrees with `matches`, the in-memory evaluator,
  over the whole set; set algebra over two readings equals the combined reading.
- **Reporting in SQL.** Totals, balances, a grouping four levels deep (journal, account,
  partner, month) equal to a `GROUP BY` written by hand; a bucket opens to exactly the rows it
  counted; a `NULL` partner is a bucket of its own.
- **Transactions.** A batch with a savepoint per group keeps what posted and undoes only the
  group that did not; a unit of work that raises leaves nothing; the second of two writers is
  `StaleAggregate` in one process and across two; `updated_at` is stamped on update only;
  a lock outside a unit of work is refused; row locks hold on Postgres.
- **The event chain.** One command posts, reporting moves the accounts, notifications hears
  each `BalanceUpdated` with the command's `correlation_id` and the `EntryPosted` as its
  `causation_id`; a refusal publishes nothing; the typed publish waits for reporting's answer;
  the chain crosses a process boundary through a `BackgroundQueue`.
- **The async door.** Thirty postings at once through `get_async_bus()` leave every balance
  right; the async publisher answers typed.

## Timings, for reference

From one run on a laptop, SQLite file, WAL:

| Case | 2 000 entries | 25 000 entries |
|---|---|---|
| populate | 0.25 s (8 083 rows) | 3.2 s (100 087 rows) |
| keyset walk, pages of 500 / 1 000 | 0.06 s | 0.48 s |
| grouping, four levels | 0.26 s | 2.7 s |
| concurrent postings, async bus | 30 in 0.54 s | 100 in 0.81 s |

The grouping number is what changed while this suite was written: resolving one statement per
bucket per level took 3.4 s at the small volume; one statement per level takes 0.26 s.
