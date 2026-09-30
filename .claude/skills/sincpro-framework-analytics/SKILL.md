---
name: sincpro-framework-analytics
description: Read data and compose behaviour with sincpro_framework — DataFrames fed from a Criteria for pandas/polars/DuckDB, use cases stored as source and loaded onto the bus without a deploy, and Commands composed as JSON workflows. Use whenever a task analyses data, exports to a dataframe, loads/replaces a use case at runtime, or builds a workflow/automation out of existing Commands.
---

# sincpro-framework-analytics

Three utilities around the bus. Each is opt-in and needs little beyond the bus.

## Data analysis — read a query once

`sincpro_framework.data_analysis` holds what a `Criteria` answered as a `DataFrame`, so the same
question is not sent twice. It is a utility: it holds, merges and hands on; the analysis is the
dataframe library's. `pip install sincpro-framework[data-analysis]`.

```python
from sincpro_framework.data_analysis import QueryCache

cache = QueryCache(max_rows=2_000_000)
cache.fetch(repository, InvoiceLine, posted)                  # one page
cache.fetch(repository, InvoiceLine, posted, pages=2)         # reads only the missing page
complete = cache.fetch_all(repository, InvoiceLine, posted)   # every row
narrower = complete.narrow({"field": "journal", "operator": "=", "value": "SAL"})   # no read
```

- A read is held under the repository's **fingerprint** (filter, order, mask, scope), never the page.
- `narrow` answers one more condition with no read, but only on a **complete** frame
  (`NotComplete` otherwise).
- Hand off: `to_arrow()` / `to_parquet()` / `to_ipc()` / `to_json()`, or straight into polars, pandas,
  DuckDB. `DataFrame.from_arrow(table)` comes back.
- `invalidate_on_commit(database, cache, *aggregates)` lets go of a read when a write commits.

Full detail: [references/data-analysis.md](references/data-analysis.md), `docs/data_analysis/README.md`, PRD_09.

## Runtime use cases — source on the bus

`sincpro_framework.runtime_use_cases` keeps a Command, a Response and the Feature that answers them
as **source**, in a table or in memory, and loads them onto a bus without a deploy. A `BusRegistry`
builds a **new generation** of the bus (`bus.fresh()` plus the store), checks it by building it, and
swaps it in whole; a refused generation leaves the bus as it was.

```python
from sincpro_framework.runtime_use_cases import BusRegistry, InMemoryUseCases, RuntimeUseCase

store = InMemoryUseCases()
store.save(RuntimeUseCase(name="quote", source=QUOTE))
registry = BusRegistry(billing, store)
registry.execute("sincpro_runtime.billing.quote.CommandQuote", {"amount": 100})
```

A stored handler may `replaces=` one in code; a stored source run with the service's permissions — keep
saving behind the same review a deploy has. `check()` before saving, `put()` = check+save+swap
atomically, `check_all()` in CI.

Full detail: [references/runtime-use-cases.md](references/runtime-use-cases.md), `docs/runtime_use_cases/README.md`, PRD_06.

## Workflows — Commands composed as data (experimental)

A workflow composes the Commands a bus already answers as JSON — `execute`, `code`, `for_each`,
`fail`, `when` — validated against the live bus, run with a trace, drawn as a graph.

```python
from sincpro_framework.workflows import FileWorkflows, Workflows

workflows = Workflows(billing, FileWorkflows(Path("workflows")))
workflows.expose()                                    # CommandRunWorkflow on the bus
run = workflows.run("bill_order", {"order_id": 1})    # run.steps is the trace
```

A set with any issue never replaces the one in force (`reload()` answers the issues); `refresh()`
reloads only when the source changed — cheap enough for a cron. For an agent: `Workflows.schema()`,
`workflows.catalog()`, `validate(draft)`, `dry_run(draft, responses=…)`.

Full detail: [references/workflows.md](references/workflows.md), `docs/workflows/README.md`, PRD_07.

## References

- [references/data-analysis.md](references/data-analysis.md) — `QueryCache`, `DataFrame`, narrow, hand-off
- [references/runtime-use-cases.md](references/runtime-use-cases.md) — `BusRegistry`, generations, stores
- [references/workflows.md](references/workflows.md) — the JSON vocabulary, validation, dry run

## Related

- The `Criteria` these read: `sincpro-framework-criteria`
- A cache of a bus Query's answer across replicas (different thing): `sincpro-framework-caching`
