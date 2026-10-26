# Persistence specification

What a Sincpro service gets when it declares an aggregate: how the entity is described, how it is
read, grouped and analysed, how it is written, and what is still missing. Updated in place with
every change (see [how a specification is kept](README.md)). Deep reasoning for the older
decisions is in [`../persistence/decisions.md`](../persistence/decisions.md) and the state of the
art in [`../persistence/landscape.md`](../persistence/landscape.md).

**Status:** ✅ built and held by a test · 🟡 partly · ⬜ not built.

## 1. The entity model

An aggregate is a dataclass that extends `Entity`; its table is written by the project
(`entity_table` prepends the four base columns) and mapped with `map_aggregates`. Everything below
is read from the class, once: `Meta` is the single description a repository, a client and a
screen share.

| Part of the model | Status | What it is | Held by |
|---|---|---|---|
| Identity and version | ✅ | `id`, `created_at`, `updated_at`, `version`: optimistic lock, a stale write is refused | `tests/data_layer/orm/test_store.py` |
| Scalar fields | ✅ | text, integer, number, `Decimal` (exact), boolean, date, datetime, UUID, enums (`choices`), `text[]`, translated text | `tests/data_layer/orm/test_describe.py` |
| Embedded value objects | ✅ | a dataclass or pydantic model inside a row, with its own shape | `tests/data_layer/orm/test_describe.py` |
| Relations | ✅ | many2one, one2many, many2many; declared, inferred from foreign keys, or resolved through a bus; read per page | `tests/data_layer/orm/test_relations.py` |
| Mixins | ✅ | archivable (soft delete), audited, change tracking, event sourced, deliverable events | `tests/data_layer/orm/test_tracking.py` |
| **Computed field** | ✅ | `computed(sql=…, python=…)`: a value that is an expression over the row, read with it, filtered/ordered/grouped like a column, never written | `tests/data_layer/orm/test_model_driven_analytics.py` |
| Derived field | ✅ | `Derive`: stored, computed in Python on save and preview, ordered by dependency | `tests/ddd/test_one_or_several.py` |
| Form hints | ✅ | `presentation`: readonly/required and the `*_when` conditions, as criteria expressions; hints, never rules | `tests/ddd/entity/test_presentation_hints.py` |
| How the entity is read | ✅ | `DEFAULT_GET_ID`, `DEFAULT_READING`, `DEFAULT_ORDER`, `DEFAULT_DISPLAY`, `DEFAULT_LITERAL_SEARCH` | `tests/ddd/entity/test_query_entity.py` |
| What `Meta` publishes | ✅ | per field: type, `ops`, `sortable`, `groupable`, `aggregates`, `computed`, `choices`, hints; plus `resolvers` | `tests/data_layer/orm/test_field_meta_unified.py` |
| Table derived from the dataclass | ⬜ | the project writes the table; only the four base columns are given | — |
| Indexes declared on the entity | ⬜ | an `Index` is passed to `entity_table` by hand | — |
| Money (amount + currency) | 🟡 | `Decimal` is exact; there is no currency type or conversion | — |

### Why a computed field has two implementations

`sql` is what a database runs, `python` is what a store in memory answers. One is not derived from
the other: a lambda over SQLAlchemy columns cannot be executed on a record, and Python cannot be
turned into SQL. Two implementations of one value drift, so the framework gives a test
(`ComputedFieldsContract`) that puts both in front of the same rows, and makes `python` optional:
without it a memory store **refuses** to filter or group by the field instead of guessing it.
Rejected: deriving one from the other (it works for `a - b` and fails at the first `CASE`),
and computing it in the hooks and storing it (that is `Derive`, which cannot be filtered by a value
nobody wrote yet, and goes stale).

## 2. Reading

| Capability | Status | Notes | Held by |
|---|---|---|---|
| Filter language | ✅ | `Condition`, `All`, `Any`, `Not`; operators by type; a value is a literal or a dynamic value | `tests/ddd/criteria/test_criteria.py` |
| Dynamic values | ✅ | `today`, `now`, `period` (named or a window), `context`, `param`, `field`; stored symbolic, read by the server; UTC | `tests/ddd/criteria/test_resolve.py` |
| Column against column | ✅ | `FromField`; same type, comparing operator | `tests/ddd/criteria/test_resolve.py` |
| Ordering, keyset and offset pages | ✅ | a nullable column is refused for ordering; a cursor is opaque and typed | `tests/ddd/criteria/test_pagination.py` |
| Specification (what to bring) | ✅ | field → criteria, at any depth; the identity always travels | `tests/ddd/criteria/test_specification.py` |
| Same answer in memory and SQL | ✅ | one parity file run through both evaluators and the TypeScript client | `tests/ddd/criteria/test_criteria_parity.py` |
| Built-in reads | ✅ | `EntityReads`: get, get many, select by text, list, history | `tests/ddd/reads/test_reads.py` |
| Forms over several requests | ✅ | `Edit`: keep, save, discard a draft held by the context | `tests/ddd/editing/test_edits.py` |
| Wire contract | ✅ | JSON Schema generated from the models; hand-written resolution cases | `tests/ddd/criteria/test_criteria_contract.py` |
| Rules bound to the request (tenant, company) on every read | ⬜ | `narrowed` is applied by hand | — |
| As-of reads | ⬜ | | — |

## 3. Analysis from the model

The goal: **define the model and get the analysis**, without writing a query. A criteria with a
`grouping` is answered by one statement per level, in memory or SQL, with the same numbers.

| Capability | Status | Notes | Held by |
|---|---|---|---|
| Group by a field, fold with `measures`, `where_measures` (having) | ✅ | `sum avg min max count count_distinct percentile` | `tests/data_layer/orm/test_grouping.py` |
| Group by a **computed field** | ✅ | `GroupingLevel(field="balance")`; folds over it too | `tests/data_layer/orm/test_model_driven_analytics.py` |
| Date **grain** | ✅ | `day week month quarter year`; labels like `2026-Q3`; ranges, in UTC | `tests/data_layer/orm/test_grouping.py` |
| Date **part** | ✅ | `year quarter month iso_week day_of_week day_of_month hour`, as numbers; Monday is 1 | `tests/data_layer/orm/test_model_driven_analytics.py` |
| **Groups with no rows** | ✅ | `GroupingLevel(fill=True)`: enum choices, booleans, part numbers, a bounded date grain | `tests/data_layer/orm/test_model_driven_analytics.py` |
| More folds | 🟡 | `median`, `bool_and`, `bool_or` built; `array_agg`, `first`, `last` not | `tests/data_layer/orm/test_model_driven_analytics.py` |
| Pivot with margins | ✅ | rows × columns, margins folded in the database | `tests/data_layer/orm/test_pivot_and_explain.py` |
| `Meta.groupable` / `aggregates` / `resolvers` | ✅ | a client never guesses what a field takes | `tests/data_layer/orm/test_model_driven_analytics.py` |
| Computed over a relation (count/sum of children) | ⬜ | a correlated subquery; needs masks and cost to be settled | — |
| Named measures and dimensions on the entity | ⬜ | `margin = sum(total) - sum(cost)`; an «age band» | — |
| Totals in one statement | ⬜ | `GROUPING SETS`/`ROLLUP` where the engine has them; today one statement per level | — |
| Window measures | ⬜ | running total, rank, share of total, previous period | — |
| Grouping in the context's timezone | ⬜ | grains cut in UTC | — |
| Money conversion in a sum | ⬜ | a sum converted by the rate of a date | — |
| Volume (millions of rows, DataFrames) | 🟡 | SQL side solid; client side hydrates — see the landscape | `tests/data_layer/orm/test_duckdb_parquet.py` |

### Decisions of this section

- **A part is not a grain.** A grain cuts (a bucket is a range of time, `2026-03`) and a part
  extracts (a bucket is a number many dates share, `3`). They answer different questions, so
  they are two fields of `GroupingLevel` and a level takes one. Rejected: `grain="month_number"`, which
  hides that one of them can be opened as a filter and the other cannot.
- **A group of a part cannot be opened.** «Every March» is not a condition on the date, so its
  `criteria` is its parent's and `Bucket.openable` is `False`. The alternative, a filter that
  looked right and selected rows that are not the group's, is the bug the grouping tests were
  written against once already.
- **`fill` lists only what is knowable.** The choices of an enum, both booleans, the numbers a
  part can be, and the labels of a grain between two bounds the filter gives. A text field has
  no «every» and is refused, naming that. It cannot be combined with a filter or a page over the
  groups: those decide over groups the rows made, and an empty group has no rows.
- **Empty folds are honest.** A count over no rows is 0; a sum, an average, a minimum is `None`
  and not 0, because 0 is a value.
- **The quarter is a grain and a part.** SQLite has no quarter; it is the month rounded up to
  three. Every dialect answers the same text (`2026-Q3`) and the same number.
- **`median` is the 0.5 percentile and takes no argument**; SQLite has neither and refuses by
  name, a memory store answers both.
- **Numbers sort as numbers in memory** as in SQL: a group `10` follows `9`.

## 4. Writing

| Capability | Status | Notes | Held by |
|---|---|---|---|
| Unit of work, savepoints, `retrying` | ✅ | one transaction per `context()`; what the engine refuses is told apart, and `retrying` repeats a transient failure | `tests/data_layer/orm/test_transactions.py` |
| Cascade by assignment, upsert, bulk, writes by criteria | ✅ | | `tests/data_layer/orm/test_cascade.py`, `test_bulk_writes.py` |
| `after_commit` hooks and a transactional event log | ✅ | | `tests/data_layer/orm/test_commit_hooks.py` |
| Gapless numbering per series | ✅ | `DatabaseNumbering`: a counter row taken in the unit of work | `tests/data_layer/orm/test_numbering.py` |
| Events as entities, relay, event sourcing | 🟡 | snapshots, projection rebuilds and sagas are not built | `tests/data_layer/orm/test_events_table.py` |
| Rules checked on every write (`check_company`) | ⬜ | | — |
| Attachments, erasure (GDPR) | ⬜ | | — |

## 5. Stores

| Store | Status | Notes |
|---|---|---|
| Memory (`MemoryRepository`) | ✅ | the same vocabulary without a database, a unit of work included (commit, undo, put back what was not saved, `for_update` / `skip_locked` / `nowait` across threads — `tests/data_layer/repositories/test_memory_unit_of_work.py`); no relations, no isolation between blocks, no date **grains** (a grain is the adapter's), parts and fills yes |
| SQLAlchemy | ✅ | SQLite always; PostgreSQL when `SINCPRO_POSTGRES_URL` is set — the analysis suite (`test_model_driven_analytics.py`) runs on both, with the same rows; DuckDB has translators and compile-level tests |
| Another adapter (document, key-value) | ⬜ | the capabilities are declared; no second adapter yet |

## 5b. Extending an entity you do not own

Specified in [extensibility](extensibility.md): the rule (what an extension adds, the extension owns),
what it may do to an entity, which mechanism to use when, how a release of the core or of an
extension is survived, the measurements on PostgreSQL, and what is still missing. A mapped entity
answers each row as the most derived extension it has (the type is worked out from the rows, never
stored), an extension may keep its columns in the entity's own table, sibling extensions can be one
`Consolidated` record, and the migrations of two chains that declare one table leave each other's
columns alone. The memory store has none of it: an extension is a mapped class.

## 6. Known limits

- A computed field with only `sql` is invisible to a memory store (it refuses, it does not guess).
- A record not yet read from a store holds the field's default (`None` once the class is mapped).
- A date compared with an instant column is that date's midnight in the context's timezone,
  as a UTC instant; grain buckets are cut in UTC.
- Date-part SQL for DuckDB is verified by compilation only. PostgreSQL's runs against a server when
  `SINCPRO_POSTGRES_URL` is set (it passes on PostgreSQL 17); this repository's default run is SQLite.
- A membership test with 2,000 values or more (`in` / `not in`) travels on PostgreSQL as one array
  parameter, `= ANY(:values)`, because a statement accepts 65,535 parameters; elsewhere it is an
  ordinary `IN`.
- Running the whole `tests/data_layer/orm` folder against PostgreSQL in one process fails for reasons
  that predate this work (the same files fail at `c8c89aa`); run it file by file. `test_events_table.py`
  fails on PostgreSQL either way.
- SQLite's `strftime` reads ISO formats (`%G`, `%V`, `%u`) only from 3.46 (May 2024), and a Python
  built on an older library answers NULL for them. The framework therefore never uses them on
  SQLite: the ISO week is computed from the Thursday of its week and the weekday from `%w`, and a
  test compares both with Python's `isocalendar` over fifty years of days.
