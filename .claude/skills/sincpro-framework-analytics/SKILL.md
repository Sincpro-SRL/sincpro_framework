---
name: sincpro-framework-analytics
description: Read data and change behaviour with sincpro_framework — DataFrames fed from a Criteria for pandas/polars/DuckDB, and use cases stored as source and loaded onto the bus without a deploy. Use whenever a task analyses data, exports to a dataframe, or loads/replaces a use case at runtime.
---

# sincpro-framework-analytics

Two opt-in utilities around a bounded context's bus: **data analysis** (what a `Criteria` read,
held as a frame for dataframe libraries) and **runtime use cases** (a Feature stored as source and
loaded without a deploy). This
skill stands alone; the deep docs it names live in the framework repository, not in the package.

## Context

- **Data analysis** reads the rows of a `Criteria` once, page by page, and hands them to polars,
  pandas, DuckDB or a client as Arrow/Parquet. It is not an analytics engine (no group-by, join or
  pivot of its own), not a cache shared by replicas, and not the way to compute a total: an
  aggregate the database can answer is a `Criteria` with measures/group_by.
- **Runtime use cases** change behaviour without a deploy: a Command, Response and one Feature or
  ApplicationService stored as Python source in a store, loaded onto a new generation of the bus. It
  is not a sandbox (stored source runs with the service's permissions) and not configuration (that
  is settings). When a deploy is cheap, code reviewed in a PR is simpler.

## Abstractions

| Term | What it is | Kind | Import |
|---|---|---|---|
| `QueryCache` | Reads held by the repository's fingerprint of a `Criteria`; `fetch`, `fetch_all`, `get`, `invalidate`; LRU past `max_rows` | registry | `from sincpro_framework.data_layer.data_analysis import QueryCache` |
| `DataFrame` | Columnar rows: `columns`, `types`, `complete`, `cursor`; `narrow`, `sort`, `select`, `to_arrow`/`to_parquet`/`to_ipc`/`to_json`, `from_arrow` | DTO (frozen dataclass) | `from sincpro_framework.data_layer.data_analysis import DataFrame` |
| Fingerprint | The key of a read: filter, order, mask, scope — never the page | function | `repository.fingerprint(target, criteria)` |
| `invalidate_on_commit` | Lets a cache go of an aggregate's reads when a write of it commits | function | `from sincpro_framework.data_layer.orm import invalidate_on_commit` |
| `RuntimeUseCase` | A use case as data: `name`, `source`, `version`, `active`, `replaces` | DTO | `from sincpro_framework.runtime.runtime_use_cases import RuntimeUseCase` |
| `UseCaseStore` | Where use cases are stored: `active()`, `save(use_case)` | port (abstract) | `from sincpro_framework.runtime.runtime_use_cases import UseCaseStore` |
| `InMemoryUseCases` | Store in the process | adapter | `from sincpro_framework.runtime.runtime_use_cases import InMemoryUseCases` |
| `SqlUseCases` / `use_case_table` | Store in a table of the context's database / that table on its `MetaData` | adapter / function | `from sincpro_framework.data_layer.orm import SqlUseCases, template_table` → `template_table.use_case_table` (extra `[sqlalchemy]`) |
| `BusRegistry` | The code's bus plus the stored use cases, as generations: `current`, `generation`, `in_force`, `execute`, `reload`, `check`, `put`, `check_all` | registry | `from sincpro_framework.runtime.runtime_use_cases import BusRegistry` |
| Generation | `bus.fresh()` (every code registration replayed) + stored sources, built, swapped in whole | function | `UseFramework.fresh()` |

Exceptions: `ProgrammingError` (narrowing a frame that stopped at a page), `ProgrammingError`
(`data_analysis`); `UseCaseRefused` (a stored use case that does not load — the bus stays as it
was).

Look-alikes: `QueryCache` (in-process frames for analysis) vs `QueryCaching` in
`sincpro_framework.data_layer.caching` (a bus Query's answer, shared by replicas). A **runtime use case** *is* a use case
on the bus. `registry.current` (the generation answering) vs the
context's bus `billing` (the code only, never sees a stored use case).

## Architecture

**In the framework.** `sincpro_framework.data_layer.data_analysis` (`cache` QueryCache, `frame` DataFrame,
`arrow` the hand-off) needs only the core; Arrow/Parquet need pyarrow, extra `[data-analysis]`,
imported only when asked for. polars, pandas and DuckDB are the project's own dependencies — they
take a frame through the Arrow PyCapsule interface. `sincpro_framework.runtime.runtime_use_cases` (`domain`
RuntimeUseCase + `UseCaseStore` port, `in_memory`, `loading`, `registry` BusRegistry) is stdlib; the
SQL store is `sincpro_framework.data_layer.orm` (`[sqlalchemy]`).

**In a consumer service** (one `UseFramework` bus per bounded context, created in
`infrastructure/framework.py` before `services/` is imported):

```
myapp/
  domains/billing/
    __init__.py               # 1. billing = config_billing_framework(...)  2. from . import services
    infrastructure/
      framework.py            # the bus factory
      dependencies.py         # add_dependency("frames", invalidate_on_commit(database, QueryCache(...), InvoiceLine))
      tables.py               # MetaData, incl. use_case_table(metadata, "billing_runtime_use_case")
      runtime_use_cases.py    # billing_registry = BusRegistry(billing, SqlUseCases(database, use_cases))
    services/                 # Features: a Query reading frames
  entrypoints/                # gateways only; a handler resolving billing_registry.current per request
```

**One frame read:**

```
Query Feature → self.frames.fetch_all(self.repository, InvoiceLine, criteria)
  → key = repository.fingerprint(...)       held? continue from its cursor : start empty
  → repository.search(page by keyset cursor, no count) … until complete
  → DataFrame → narrow / to_arrow → polars / DuckDB → a Response DTO (to_json(), Parquet bytes)
a write commits → invalidate_on_commit → cache.invalidate(InvoiceLine)
```

**One generation:**

```
registry.put(use_case)   |   store.save(...) then registry.reload()
  → billing.fresh() + each active source compiled as sincpro_runtime.<context>.<name>
  → build_root_bus()  ── fails → UseCaseRefused, current and store untouched (put)
  → one assignment: current = new bus, generation += 1   (requests in flight finish on theirs)
request → registry.execute("billing.CommandQuote", payload)
```

## Mistakes an agent makes

- **Treating `cache.fetch(...)` as all the data** — it holds `pages=1` page of the `Criteria`'s
  `pagination.limit` (50 by default): totals over a fraction, no error. Use `fetch_all`, or check
  `frame.complete`. `pages=` is the total held, not how many more; the `Criteria`'s own page or
  offset is ignored — a read always starts from the first row.
- **Expecting a frame to refresh** — nothing is re-read on its own, and writes by another process
  are never seen. Wire `invalidate_on_commit` in the writing process; do not hold aggregates
  another process writes.
- **Fetching every row to sum in Python** — an aggregate is a `Criteria` with measures/group_by in
  the database (`sincpro-framework-criteria`); a cross-replica answer cache is `QueryCaching`.
- **Serving from the context's bus, or mounting an entrypoint on one generation** — `billing` never
  answers a stored use case, and a stored `replaces=` silently loses to the code handler there. An
  entrypoint holds the bus object it was given and still answers that generation after a
  reload. Read `registry.current` per request, or route by name through `registry.execute`.
- **Expecting a save on one replica to reach the others** — each replica loads on its own
  `reload()` (`False` when nothing changed). Call it from a cron, or after each save.
- **Building a stored Command from its class, or holding one across a reload** — a stored Command
  is a new class per generation, answered only by that generation's bus. Reach it by name.
- **`expose()` after the bus is built** — `catalog()`, `validate()`, `current` and the first
  execution build it; `expose()` then raises. Call it in the context's `__init__.py`, right after
  `services` is imported — never from `entrypoints/`, which never registers on a bus.

## Data analysis — how

```python
from sincpro_framework.data_layer.data_analysis import QueryCache

cache = QueryCache(max_rows=2_000_000)
first = cache.fetch(repository, InvoiceLine, posted)               # one page
more = cache.fetch(repository, InvoiceLine, posted, pages=2)       # reads only page 2
sales = cache.fetch_all(repository, InvoiceLine, posted)           # every row; complete
sal = sales.narrow({"field": "journal", "operator": "=", "value": "SAL"})   # no read
by_journal = polars.DataFrame(sales).group_by("journal").agg(polars.col("amount").sum())
```

`pip install sincpro-framework[data-analysis]` for Arrow/Parquet. Full detail:
[references/data-analysis.md](references/data-analysis.md); deep doc in the framework repo:
`docs/data_analysis/README.md`, PRD_09.

## Runtime use cases — how

```python
from sincpro_framework.runtime.runtime_use_cases import BusRegistry, InMemoryUseCases, RuntimeUseCase

store = InMemoryUseCases()
store.save(RuntimeUseCase(name="quote", source=QUOTE))     # QUOTE: Command, Response, one Feature
registry = BusRegistry(billing, store)                     # nothing read or built yet
registry.execute("billing.CommandQuote", {"amount": 100})
```

`check(draft)` before saving; `put(use_case)` = check + save + swap, atomically; `check_all()` in
CI (a refactor of the code can break a stored source that nothing reloads). Saving is running
code: put it behind the review a deploy has. Full detail:
[references/runtime-use-cases.md](references/runtime-use-cases.md); deep doc in the framework
repo: `docs/runtime_use_cases/README.md`, PRD_06 and PRD_07.

## References

- [references/data-analysis.md](references/data-analysis.md) — `QueryCache`, `DataFrame`, narrow, hand-off
- [references/runtime-use-cases.md](references/runtime-use-cases.md) — `BusRegistry`, generations, stores

## Related

- The `Criteria` these read, and aggregates in the database: `sincpro-framework-criteria`
- A cache of a bus Query's answer across replicas (different thing): `sincpro-framework-caching`
- A cron that calls `refresh()` / `reload()`: `sincpro-framework-operations`
- Exposing the bus (and why an entrypoint holds one bus object): `sincpro-framework-entrypoints`
