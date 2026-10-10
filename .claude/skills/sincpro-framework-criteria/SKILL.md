---
name: sincpro-framework-criteria
description: Query with sincpro_framework — write a Criteria (filters, ordering, keyset or offset pagination, count, specification), run it on the Repository, paginate page by page, aggregate with measures/group_by/pivot/export, and resolve relations. Use whenever the task lists, filters, sorts, paginates, counts or aggregates records, or builds a screen/endpoint that reads them, in a Sincpro Python service.
---

# sincpro-framework-criteria

`Criteria` is the one object that crosses a boundary. A screen sends it, a saved reading is one,
another bounded context receives one when a relation is resolved through its bus, and a Feature
hands it to the repository. It is JSON, it is typed (a pydantic DTO), and every part of it is
optional.

This skill stands alone. The framework repo goes deeper in `docs/persistence/criteria.md`,
`specification.md` and `relations.md` (in the framework repo; not shipped with the package).

## Context

- **Problem it solves:** one way to ask for records — filter, order, page, what to bring back,
  how to count, how to group — validated against the model, so every listing, export, facet and
  cross-context relation speaks the same language and the answer says what may be asked next.
- **It is not** SQL, an ORM query builder or a reporting engine: conditions apply to one
  aggregate's own fields, with and/or/not. No joins, subqueries or computed expressions — those go
  through `repository.statement()`/`run()` or `repository.session` inside a unit of work.
- **Do not use it** to write data (writes take the aggregate: `sincpro-framework-persistence`),
  nor to cache answers (`sincpro-framework-caching`), nor for dataframe analysis
  (`sincpro-framework-analytics`, which is fed by a Criteria).

## Abstractions

| Term | What it is | Kind | Import |
|---|---|---|---|
| `Criteria` | `where`, `order`, `pagination`, `specification`, `grouping`, `count`, `meta` | DTO | `from sincpro_framework.ddd import Criteria` |
| `Condition` | One question: `field`, `operator` (default `=`), `value` | DTO | `from sincpro_framework.ddd import Condition` |
| `All` / `Any` / `Not` | Combine conditions; JSON keys `all`, `any`, `negate` | DTO | `from sincpro_framework.ddd import All, Any, Not` |
| `Operator` | `=` `!=` `in` `not in` `like` `starts with` `>` `>=` `<` `<=` `is null` `contains` `not contains` `between` — `%` and `_` are text, never wildcards | enum | `from sincpro_framework.ddd import Operator` |
| `Sort` / `parse_order` | One ordering key (`field`, `descending`) / `"-total,number"` → sorts | DTO / function | `from sincpro_framework.ddd import Sort`; `from sincpro_framework.ddd.criteria import parse_order` |
| `Pagination` | `limit` (default 50, no ceiling) + `strategy` | DTO | `from sincpro_framework.ddd import Pagination` |
| `Cursor` / `Offset` | Keyset strategy (`token`, the default) / row-skipping strategy (`rows`) | DTO | `from sincpro_framework.ddd import Cursor, Offset` |
| `Specification` | Field → nested `Criteria`: what to bring back of each record, relations included | DTO | `from sincpro_framework.ddd import Specification` |
| `Grouping` / `Level` / `Measure` | `group_by` levels (with date `grain`), named `measures`, `where_measures` | DTO | `from sincpro_framework.ddd.criteria import Grouping, Level, Measure` |
| `CountMode` | `none`, `capped` (default, stops at 10 000), `exact` | enum | `from sincpro_framework.ddd import CountMode` |
| `EntityCollection[T]` | What `search` answers: `items`, `count`, `cursor`, `dropped`, `meta` | dataclass (frozen) | `from sincpro_framework.ddd import EntityCollection` |
| `Count` / `Dropped` | `value` + `exact` / `field` + `reason` (`unknown_field`, `unsupported_operator`, `bad_value`, `not_expandable`) | DTO | `from sincpro_framework.ddd import Count, Dropped` |
| `Meta` | What the model publishes: fields, types, operators, sortable, relations, labels | DTO | `from sincpro_framework.ddd import Meta` |
| `Query` / `ResponsePaginatedQuery` | Command base carrying `criteria` / answer base with `cursor`, `count`, `entity_meta_data`, `dropped` + exactly one records field | DTO | `from sincpro_framework.ddd import Query, ResponsePaginatedQuery` |
| `Bucket` / `Pivot` | A group with its count, measures and the `criteria` that opens it / a cross table with margins | DTO | `from sincpro_framework.ddd import Bucket, Pivot` |
| `matches` | Evaluates a filter in memory, the same semantics as the SQL translator | function | `from sincpro_framework.ddd import matches` |
| `DEFAULT_READING` / `DEFAULT_ORDER` / `DEFAULT_LITERAL_SEARCH` | What an entity answers once, as criteria: what a record brings (`Specification`), the list order (`Sort`s), the template a typed text fills (`Criteria` whose `where` holds `TEXT`) — with `DEFAULT_GET_ID` and `DEFAULT_DISPLAY`. `@classmethod`s on `Entity`; a caller's criteria wins for each part it names (`Criteria.replaced_by`) | `@classmethod` | `Entity`; `TEXT` from `sincpro_framework.ddd` |
| `Presentation` | Form hints only, whose conditions are Criteria triples (`When(Is(...))`) | class attribute | `from sincpro_framework.ddd import Presentation` |
| `matching` / `detail_of` | `DEFAULT_LITERAL_SEARCH` filled with a literal / `DEFAULT_READING` as the criteria for `get(id, detail=...)` | function | `from sincpro_framework.ddd import matching, detail_of` |
| `InvalidCriteria` | A question that cannot be answered (unorderable field, bad grouping, unknown measure) | exception | `from sincpro_framework.ddd import InvalidCriteria` |

Look-alikes: `Criteria.order` orders rows, `Grouping.order` orders groups. `where` filters rows,
`where_measures` filters groups. A **measure** is a sum/count/avg; an **aggregate** is the DDD root.
`specification` says what comes back; `narrowed()` (on the repository) says what may be seen.

## Architecture

**(a) Inside the framework.** The language is stdlib + pydantic in `sincpro_framework/ddd/criteria/`
(`criteria.py`, `pagination.py`, `evaluate.py` for in-memory `matches`); `Meta` is
`ddd/entity/entity_meta.py`; `Query`/`ResponsePaginatedQuery` are `ddd/query.py`. Two stores answer
it: `MemoryRepository` (in memory, no extra) and the SQLAlchemy `Repository`, whose
`orm/sqlalchemy/services/sql_translator.py` turns a Criteria into a `Select` (extra `[sqlalchemy]`).
DataFrames over a Criteria are `sincpro_framework.data_layer.data_analysis` (extra `[data-analysis]`).

**(b) Inside a consumer service** — one `UseFramework` bus per bounded context, built in
`infrastructure/framework.py` and created before `services/` is imported:

```text
domains/billing/
  domain/invoice.py              Invoice(Entity) · Invoices(EntityCollection[Invoice])
  infrastructure/tables.py       NOT NULL columns for whatever a screen orders by, plus their index
  services/list_invoices.py      CommandListInvoices(Query) · ResponseListInvoices(ResponsePaginatedQuery)
                                 @billing.feature(CommandListInvoices) class ListInvoices(Feature)
  entrypoints/                   exposure only (REST/MCP/RPC): the Criteria arrives as JSON in the Command
```

**(c) One call — a listing:**

```text
client JSON {"criteria": {...}} → CommandListInvoices(criteria=Criteria)
  ListInvoices.execute
    page = self.repository.search(Invoices, dto.criteria)
      Meta.accept(where)        unknown field / operator / value → removed, reported in page.dropped
      order checked             nullable or unknown → InvalidCriteria (raised)
      1 statement for the page  LIMIT limit+1 (the extra row says "there is more")
      +1 capped count           free when the first page came back short
      +1 per named relation     for the whole page, never per row
    return ResponseListInvoices.of(page, dto.criteria)
  → wire: records cut by the specification, cursor, count, dropped, entity_meta_data
```

## Mistakes an agent makes

Silent ones first — the runtime gives no error for these.

- **A key the language does not have is refused.** `{"field": "total", "op": ">", ...}`,
  `{"field": "total", "desc": true}`, a top-level `"limit"`, `{"all": [...], "any": [...]}` raise
  instead of being dropped. The names are `operator`, `descending`, `pagination.limit`,
  `grouping.group_by`. A client of another version reads with
  `Criteria.model_validate(data, context=TOLERANT)`. A *field* the model does not have is still
  dropped and reported in `dropped`.
- **A filter the model cannot answer is dropped and the result widens.** A typo in a field a
  Feature adds on the server side returns *everything*. Check `page.dropped` in tests; put
  tenant/permission scopes in `repository.narrowed(...)`, which refuses instead of dropping.
- **`search(...).items` is one page** (50 rows unless `pagination.limit` says otherwise). For all
  rows: `fetch_all` (bounded sets), `stream` (pages), `pluck`/`export`, or `measures`/`group_by`
  for totals.
- **`criteria.model_copy(update={"cursor": …})` does nothing** — the page never advances and a
  loop never ends. Resume with `criteria.resuming_from(page.cursor)`.
- **`repository.stream(...)` yields pages (`EntityCollection`), not records.** Iterate
  `for page in …: for record in page: …`.
- **A hand-built response loses the contract.** Returning `items=list(page.items)` drops the
  cursor, count, `dropped`, `entity_meta_data` and the specification mask on the wire. Use
  `ResponsePaginatedQuery.of(page, criteria)`.
- **`specification: {}` is not `None`.** `None` brings every scalar and no relation; `{}` brings
  the identity and, when the entity names one, its display field.
- **`matching` does not search.** It builds the criteria; `search(Model, matching(Model, text))`
  runs it. A blank literal drops the conditions holding `TEXT` and lists the first 8 in the
  list order; an entity with nothing to search answers an empty page. A literal is read as itself: `%` and `_` are not wildcards.
- **An entity's `DEFAULT_*` are checked when the class is described.** A field that does not
  exist, a template without `TEXT`, a reading that is not a `Specification` or a prefix on a
  number is refused naming it. Form hints in `presentation` name fields through the entity
  (`lambda a: a.code`).
- **A screen that reads one entity** (get, get many, the select, the list) registers its DTOs
  on one `EntityReads[Entity]` instead of writing four Features: recipe in
  `sincpro-framework-persistence` → `references/entity-reads.md`.
- **`Offset` for a listing people page through** repeats and skips rows under concurrent inserts.
  Keep the default cursor; `Offset` is for a one-off read and for paging groups.
- Loud, but common: ordering by a nullable or unknown field raises `InvalidCriteria`; folding a
  page (`page.sum_by(...)`) raises `ContractViolation` — ask `repository.measures(...)`.

## The one object

```python
from sincpro_framework.ddd import Criteria

big_ones = Criteria.model_validate({
    "where": {"field": "total", "operator": ">=", "value": 75},
    "order": [{"field": "total", "descending": True}],
    "pagination": {"limit": 20},
    "specification": {"number": {}, "total": {}, "customer": {"specification": {"name": {}}}},
})
page = repository.search(Invoices, big_ones)

page.items          # the records (typed aggregates, every field in-process)
page.count          # Count(value, exact) — capped by default; None when count="none"
page.cursor         # the next page's token, or None
page.dropped        # what the model could not answer, with a reason
page.meta           # every field and relation, with types, operators, sortable
next_page = repository.search(Invoices, big_ones.resuming_from(page.cursor))
```

A Feature that serves a listing:

```python
class CommandListInvoices(Query): ...


class ResponseListInvoices(ResponsePaginatedQuery):
    invoices: list[Invoice]                     # exactly one field of its own


@billing.feature(CommandListInvoices)
class ListInvoices(Feature):
    def execute(self, dto: CommandListInvoices) -> ResponseListInvoices:
        page = self.repository.search(Invoices, dto.criteria)
        return ResponseListInvoices.of(page, dto.criteria)
```

## The rules that matter

- **One door for reads.** Reads go through the repository with a `Criteria`, so a cross-cutting
  concern — access masks, tracing, caching — is one place, not an audit of endpoints.
- **Pagination is keyset by cursor by default.** A cursor names a row, not a position, so a row
  inserted meanwhile shifts nothing. The identity is appended to every ordering as a tiebreaker.
- **Only NOT NULL fields are orderable** (nor lists or translated text): a keyset over a nullable
  column silently drops rows, so it is refused. Give each ordering a composite index in that
  order with `id` last, or the cursor buys nothing.
- **A count is capped by default** (10 000, `count.exact is False` past it) and free when the
  first page came back short. `count: "exact"` pays the full price; `"none"` skips it.
- **`dropped` applies to `where` and `specification`, never a 400.** `order`, `grouping` and
  measures that cannot be answered raise `InvalidCriteria` instead.
- **A specification is a mask** and can only take away, at any depth — safe as a permission.
- **`Meta` is reflexive** (`meta: true` by default): the answer says what to ask next, so a client
  never carries a schema of its own.

## References

- [references/criteria-grammar.md](references/criteria-grammar.md) — JSON and Python forms, operators by type, merging
- [references/pagination-and-count.md](references/pagination-and-count.md) — cursor vs offset, walking pages, count tiers
- [references/aggregation-and-analysis.md](references/aggregation-and-analysis.md) — `measures`, `group_by`, `group_by_levels`, `pivot`, `export`, `explain`, escape hatch, `narrowed`, DataFrames

## Related

- The aggregate, table, repository and relations' declaration: `sincpro-framework-persistence`
- DataFrames fed by a Criteria (pandas/polars/DuckDB): `sincpro-framework-analytics`
- Exposing a listing over REST/MCP/RPC: `sincpro-framework-entrypoints`
