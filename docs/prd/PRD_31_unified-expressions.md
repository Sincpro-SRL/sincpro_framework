# PRD 31 — One expression, one home: filters, gates and dynamic values

- **Status**: phases 1 and 2 built in the backend, 2026-10-12 — not committed (§14). Phases 3 and 4, and
  the TypeScript side, are still proposed. Open decisions in §10.
- **Builds on**: PRD_22 (context), PRD_26/27 (presentation hints, default reads), PRD_06
  (runtime workflows). Breaking changes accepted; everything ships in 3.x.
- **Goal**: whatever asks «does this hold?» or «which values?» — a filter, a form hint, a workflow
  step, a permission, a saved view — is written **once, in one language, from one package**
  (`ddd/criteria/`), serialises to plain JSON, and is understood by any client that can read JSON.

## 1. The problem, measured

| Symptom | Where |
|---|---|
| Two vocabularies for the same tree | `Condition/All/Any_/Not` (criteria) and `Is/AllHold/AnyHolds` (presentation), glued by `entity_meta._condition` |
| Seven walkers over the tree, each with its own `match` | `evaluate.holds`, `sql_translator.clause_over`, `conditions_of`, `with_text`, `entity_meta._condition`, and `condition_fields` / `condition_values` over **raw dicts** in workflows |
| One dynamic value, special-cased | `TEXT = "${text}"` + `with_text`: a sentinel with its own walker |
| A value can only be a literal | workflows made `field` a reference (`$steps.order.total`) to compare two things; `due < today` is impossible, so a Feature takes `today: date` and freezes it |
| Date logic outside the language | `Level.grain = "month"` (grouping) and `cron/Clock` each know about time on their own |
| `criteria.py`, 817 lines | operators, tree, sort, grouping, pivot, specification, parsing, text template |

Adding a node today means editing every walker. Adding a dynamic value means editing the model.

## 2. What others do (research, 2026-10-12)

Primary sources read unless marked. Odoo claims are as reported by a summarising fetch.

| System | Take | Leave |
|---|---|---|
| Odoo `orm/domains.py` | one immutable AST; each node has `_to_sql` and `_as_predicate`; optimizer levels with a separate `DYNAMIC_VALUES` level; wire stays the legacy list | dynamic values as Python source strings (`context_today() + relativedelta(...)`) — not portable, not evaluable in TS; JS and Python evaluators drifted |
| Django | `Now()` is a node, not a literal; `resolve_expression` (bind) apart from `as_sql` (compile) | no in-memory evaluation of a `Q` |
| SQLAlchemy | renderer registry outside the node classes (`@compiles`), so the tree is pure data | `hybrid_property`: one body, two meanings, silent drift |
| Notion | closed vocabulary of relative values (`this_week`, `one_week_ago`) resolved at query time | no «last N days» |
| Elasticsearch | date math as anchor ± offset / rounding | `M` vs `m` — use named units |
| Salesforce SOQL *(background, page 403)* | parametrised literals `LAST_N_DAYS:n`, evaluated in the user's timezone | an embedded-colon string as the wire |
| JSON Logic | one-key node shape, trivial to implement in any language | untyped coercion, no dates |
| CEL | a type-check pass before evaluation; Timestamp and Duration as types | a text syntax as the wire |
| Ibis | typed nodes validated at build time; per-backend rewrite before compile | — |

**Odoo 20, read from the source** (`odoo/tools/date_utils.py::parse_date`, `odoo/orm/domains.py`,
`addons/web/static/src/core/tree_editor/utils.js`, `virtual_operators.js`): relative dates are a
string DSL, `relative_date := ('today' | 'now')? offset*`, with offsets `+3d`, `-1m`, `=1d` (set a
part and truncate what is below) and weekdays (`=monday`, `+monday`, `=week_start`). It is resolved
in its own optimizer level (`DYNAMIC_VALUES`), from «now» in the **user's timezone**, and the result
is a naive **UTC** value. The domain editor's presets are all half-open ranges written in that DSL:
`last30Days = [today -30d, today)`, `monthToDate = [today =1d, today +1d)`,
`lastMonth = [today =1d -1m, today =1d)`, `yearToDate = [today =1m =1d, today +1d)`. Quarters are
the one thing the DSL cannot say, so the editor falls back to a `context_today()` Python expression.
A date `>` is rewritten `>= +1 day` and `<=` as `< +1 day`: half-open is how Odoo itself normalises.

| Other system (primary sources) | Take | Leave |
|---|---|---|
| Cube `timeDimensions` | one object carries the range **and** the bucket (`dateRange` + `granularity`); `timezone` lives in the query; unknown range = refused | English phrases parsed by a library (`from 7 days ago to now`) |
| Directus | `$NOW(-1 year)`, `$CURRENT_USER` as sentinels in the value slot | an offset with no snapping; no «start of month» |
| Frappe `Timespan` | a closed, enumerable vocabulary returning a closed range | an unknown string silently returns `None` |
| Metabase MBQL | `time-interval` = `current`/`last`/`next` + unit; a separate offset form | positional arrays with the schema commented out |
| OData `$apply` | filter / groupby / aggregate as ordered steps; typed Date vs DateTimeOffset with durations | infix strings in a URL |

Still not verified: Directus' resolution site, Cube's absolute-array `dateRange`, a literal OData
`now() sub duration'P30D'`, Metabase's grouping clauses.

## 3. Principles

1. **The tree is data.** Pydantic models, no callables, no expression strings. The wire, the JSON
   Schema and the client types all derive from it.
2. **A dynamic value is a node, stored symbolic, resolved when used.** A saved filter that says
   `today` means today whenever it runs.
3. **Resolve, evaluate, compile are three phases.** `resolve` turns a tree into one holding only
   literals; memory and SQL read only resolved trees.
4. **One walker.** Every pass over the tree goes through one traversal.
5. **The server resolves.** A client that only *sends* needs no resolver: it sends `{"$": "today"}`.
   A client that *evaluates* locally (a form hint) follows the shared test vectors (§7).
6. **Closed operators, open resolvers.** Operators reach SQL, so the set is a sealed `StrEnum`;
   a project can add a resolver (`current_branch`) without touching the framework.
7. **Facilitate, never enforce.** A gate (`readonly_when`) stays a hint; the same tree used as a
   filter stays a filter. No new rule is imposed by sharing a language.

## 4. The wire

The existing grammar stays: a condition is `{field, operator, value}`; a group is `{all}`,
`{any}` or `{negate}`; named parts, never positional. (The triple `["field", "=", v]` is authoring
sugar in TS and never travels — `project_criteria_authoring_form`.)

**New: a value is a literal or a dynamic value.** A dynamic value is an object with the reserved
key `"$"` naming the resolver; the other keys are its typed parameters. No literal can collide
with it, and a JSON Schema `oneOf` over `"$"` is enough for any code generator.

```json
{"field": "due",        "operator": "<",  "value": {"$": "today"}}
{"field": "created_at", "operator": ">=", "value": {"$": "today", "plus": {"days": -30}}}
{"field": "closed_at",  "operator": "between",
 "value": {"$": "period", "name": "this_month"}}
{"field": "owner_id",   "operator": "=",  "value": {"$": "context", "key": "user_id"}}
{"field": "total",      "operator": ">",  "value": {"$": "field", "name": "credit_limit"}}
{"field": "code",       "operator": "like", "value": {"$": "param", "name": "text"}}
```

### Resolvers shipped with the core

| `$` | Parameters | Resolves to |
|---|---|---|
| `today` | `plus?`, `start_of?` | a date in the context's timezone |
| `now` | `plus?`, `start_of?` | a datetime (UTC instant) |
| `period` | `name` (`StrEnum`: `today`, `this_week`, `this_month`, `this_quarter`, `this_year`, `last_*`), `plus?` | a half-open pair `[start, end)` — only with `between` |
| `context` | `key` | a value from `use_context()` (user, tenant, language, any declared key) |
| `param` | `name` (a path: `text`, `steps.order.total`) | a value the caller supplied for this call; replaces `TEXT` and the workflow `$steps.…` reference |
| `field` | `name` | another column of the same row, in memory and SQL |

`plus` is `{unit: integer}` with `unit` a `StrEnum` (`days`, `weeks`, `months`, `years`, `hours`,
`minutes`); `start_of` is a `StrEnum` of the calendar units: `day`, `week` (first weekday from the
context), `month`, `quarter`, `year`. `quarter` is what Odoo's DSL cannot say. Named units only: no `M`/`m`. `Level.grain` uses the same
`StrEnum`, so grouping by month and filtering «this month» share one definition of a month.

### Time arithmetic: any date-valued resolver can be shifted

`plus` and `start_of` are not a feature of `today`: they are allowed on **any resolver whose
output is a date or a datetime** — `today`, `now`, `context`, `param`, `field`. The type pass
(§6) refuses them on anything else. So «three months before the report date the user picked» is
the same two keys on a value read from the context:

```json
{"$": "today", "plus": {"days": -30}}                                    30 days ago
{"$": "today", "start_of": "month"}                                      first day of this month
{"$": "now",   "plus": {"hours": -2}}                                    two hours ago
{"$": "context", "key": "report_date", "plus": {"months": -3}, "start_of": "month"}
```

Rules, so every language computes the same instant:

1. **Order**: `plus` first, then `start_of` (the Elasticsearch `now-1d/d` order).
2. **One way to subtract**: `plus` with a signed integer. The wire has no `minus`.
3. **Calendar units** (`days`, `weeks`, `months`, `years`) move by calendar in the context's
   timezone; `hours` and `minutes` are elapsed time. A day across a DST change is still a day.
4. **Month end clamps**: 31 January + 1 month = 28 (or 29) February, as `relativedelta` does.
5. **Week start** comes from the context (Monday by default).
6. **No `end_of`.** The end of a span is the start of the next one: `start_of: "month"` with
   `plus: {"months": 1}`. Ranges are half-open, so the end is never inclusive by accident.
7. The result is a **date** against a date field and an **instant** against a datetime field;
   `today` against a datetime field means «from midnight in the context's timezone».

Ranges have two spellings, because they answer two questions:

| Question | Spelling |
|---|---|
| a calendar span: this month, last quarter | `{"$": "period", "name": "this_month"}` |
| a rolling window: the last 30 days, the next 7 | `{"$": "period", "unit": "days", "last": 30}` / `"next": 7` |
| anything else | two conditions, `>= start` and `< end` |

### Who resolves: the server, always; the client only builds the symbol

`when.today()` (or whatever the TS namespace ends up called) **computes nothing**: it returns
the object `{"$": "today"}`. The server resolves it with its own clock, its own timezone and its
own context. Why the client never sends it processed:

1. **One clock.** A client's clock and timezone are wrong often enough to matter.
2. **A saved view stays alive.** What is stored is the symbol; a processed date is frozen.
3. **The context is the server's.** `{"$": "context", "key": "user_id"}` is read from the
   server's `use_context()`, never from anything the client sends — a client cannot say «I am
   another user». (`param` is the opposite: values the caller legitimately supplies.)
4. **The cache** keys on the tree resolved by the server (§6).

A client may still send a literal date: that is the explicit choice «this exact day», frozen on
purpose (Odoo's PR 293540 draws the same line between a picked period and a typed range). A
client that evaluates locally (a form hint, filtering rows it already holds) resolves with its
own `resolve` and the shared vectors; that result stays on the client.

### What the server publishes

`describe` / `Meta` gains `resolvers`: each name, its parameters and the type it produces.
The keywords a UI offers («Today», «This month», «My branch») are not hardcoded in the client:
the backend owns the list and the client builds its dropdown from it, so a keyword a project
registers appears everywhere with no client release.

```json
"resolvers": [
  {"name": "today", "returns": "date", "params": {"plus": "span", "start_of": "unit"}},
  {"name": "period", "returns": "range", "names": ["this_week", "this_month", "last_month"]},
  {"name": "current_branch", "returns": "text"}
]
``` Every
tool that builds a filter UI reads it; so does the JSON Schema. A response may carry
`resolved_at` and the resolved filter, so a client can show what «today» meant.

## 5. Authoring, per language

Python — the nodes are the classes; no helper functions:

```python
Condition(field="due", operator=Operator.LT, value=Today())
Condition(field="created_at", operator=Operator.GTE, value=Today(plus=Span(days=-30)))
Condition(field="closed_at", operator=Operator.BETWEEN, value=Period(Periods.THIS_MONTH))
Condition(field="owner_id", value=FromContext(key="user_id"))
```

Python — a node is immutable and offers `plus`, `minus` and `start_of`, each returning a new
node (the wire only ever carries the signed `plus`):

```python
Today().minus(days=30)
Today().start_of(Unit.MONTH)
FromContext(key="report_date").minus(months=3).start_of(Unit.MONTH)
```

TypeScript (`@sincpro/criteria`) — the triple stays the sugar, and the value side gets a
namespace, like `where` and `measure`. `minus` and `startOf` are written sugar that
`readFilter` turns into the wire (`plus` signed, `start_of`), exactly as a triple becomes a
condition:

```ts
when.today({ minus: { days: 30 } })
when.today({ startOf: "month" })
when.context("report_date", { minus: { months: 3 }, startOf: "month" })
```

The earlier examples, for reference:

```ts
["due", "<", when.today()]
["created_at", ">=", when.today({ plus: { days: -30 } })]
["closed_at", "between", when.period("this_month")]
["owner_id", "=", when.context("user_id")]
```

`readFilter` converts to the wire before leaving, as it does today.

## 6. Phases of use

```
 author ──► Expression (symbolic, serialisable, stored as is)
              │  resolve(expr, ResolutionContext)        ← the clock, the timezone, use_context(), params
              ▼
        Expression (literals only)
         ├─► evaluate.holds   (memory)
         └─► sql_translator   (SQL)
```

**One instant per resolution** (Cube resolves every node against one `now`): a `ResolutionContext`
reads its clock once when it is built, so `today` and `now` in the same filter agree.

`ResolutionContext` = a `Clock` (injected; the cron `Clock` protocol moves to `common/`), the
timezone and first weekday, `use_context()`, and the call's `params`. The repository resolves at
its boundary, so a Feature writes `Condition(…, value=Today())` and receives no `today` argument.
A test pins the clock and is deterministic.

**Cache.** `QueryCaching` and `repositories/fingerprint.py` read the tree. The key must be computed
from the **resolved** tree; a key over the symbolic one serves yesterday's answer for `today`.
The first test to write.

**Type check.** A resolver declares its output type; describing the class (or validating a
criteria) checks it against the field: `today` only against a date field, `period` only with
`between`. A mismatch names the field and the resolver.

### Which context, and what grouping does

**`context` is the framework's own** (`use_context()`, PRD_22), not a second one inside criteria.
`ddd/criteria/` stays pure data and imports nothing from it. It defines only what `resolve`
needs — a clock, a timezone, a way to read a key, the call's params — and the repository, which
already imports `context`, builds that from `use_context()` at its boundary.

**Grouping is untouched.** `group_by`, `measures`, `where_measures`, `Pivot` and `Bucket` keep
their shapes. The differences: `Level.grain` draws from the shared `Unit` enum; `where`,
`where_measures` and specification nodes accept dynamic values and are resolved with the rest of
the tree; and a `Bucket.criteria` stays concrete (a bucket is «March 2026», a fact, not a
moving window). Grain cuts stay in UTC; cutting by a context timezone is a later decision.

## 7. Conformance across languages

There is already a mechanism, and this PRD extends it instead of inventing another:
`tests/ddd/criteria/test_criteria_parity.py` writes `criteria-parity.json` (25 cases, all 13
operators; `make criteria-parity`), and `sincpro_criteria` runs a **copy** of it in
`tests/parity.test.ts` (`sincpro_criteria/engine/evaluate.ts` is a second implementation of the
same evaluator). The copy is by hand today. Changes:

- The file grows two kinds of case: `{expression, context, record, expected}` for evaluation and
  `{expression, context, resolved}` for resolution (a pinned clock, a timezone, a `use_context`).
  NULL, 23:30 in La Paz, a month rollover and a relation are in it from the first version.
- The contract is **generated, never written twice**: `make criteria-contract` emits
  `Criteria.model_json_schema()` as `criteria.schema.json` and the vectors, and copies both into
  `sincpro_criteria` (today the schema is handwritten and `schema.test.ts` guards it).
- Python (memory), the SQL translator (SQLite) and TypeScript all run the same vectors.

This is what stops the Odoo JS/Python drift.

## 8. Where everything lives

```
ddd/criteria/
  expression.py     Condition, All, Any_, Not
  values.py         Today, Now, Period, Context, Param, Field, Span + the resolver registry
  resolve.py        resolve(expression, ResolutionContext)
  evaluate.py       memory
  walk.py           the one traversal (replaces seven)
  grouping.py       Level, Measure, Grouping, Bucket, Pivot
  specification.py  Specification
  sort.py  pagination.py  strict.py
```

`from sincpro_framework.ddd.criteria import …` keeps every public name. The SQL translator stays
in the ORM and consumes resolved trees. `presentation.py` keeps `When` only: a pairing of an
`Expression` with the fields a hint covers. Workflow `step.when` and permission conditions are the
same `Expression`, run with another context (a step's scope, a principal).

## 9. Breaking changes and consumer notes

| Change | Who is touched |
|---|---|
| `Is`, `AllHold`, `AnyHolds` removed; `When` takes an `Expression`; `Fields` returns a `str` subclass so `Condition(field=i.state, …)` still checks the name | any entity with `presentation` (Forge: 9) |
| `TEXT` / `with_text` → `Param("text")` | entities with `DEFAULT_LITERAL_SEARCH` |
| Workflow references in `field` → `Param` in `value`; `field` is always a field | stored workflows (a migration of stored JSON) |
| `criteria.py` split | none: the import path is unchanged |
| `today: date` arguments in repository methods become `Today()` | consumers' Features, optional |
| `@sincpro/criteria` gains `when.*`, the vectors file, the schema | web and mobile clients |

Consumers (`sincpro_mcp_odoo`, `sincpro_internal_mcp`, `sincpro_siat_soap`, `sincpro_payments_sdk`)
are updated by the architect; each break is listed here, none is applied silently.

## 10. Open decisions

| # | Question | Leaning |
|---|---|---|
| 1 | `between` over a `period`: half-open `[start, end)` always, or inclusive for dates? | half-open; the only rule that is right for datetimes |
| 2 | Default timezone and first weekday | **decided**: UTC always. Instants are UTC on the wire, in storage and in SQL; a timezone in the context only decides where a calendar boundary falls (`start_of: day` in La Paz) and is converted to a UTC instant before anything runs. Monday first |
| 3 | Resolver registry: open from phase 2, or a closed set first? | closed set in phase 2, registry in phase 4 once two real extensions exist |
| 4 | `context` reads any key, or only keys declared in `context_schema`? | declared keys; a typo is refused when the filter is validated |
| 5 | Should a stored view carry `resolved_at`, or only the response? | only the response |
| 6 | Does `field` (compare two columns) ship now? | yes — workflows need it and it is one node |
| 7 | Dynamic values in `Grouping.where_measures` and `Specification` nodes | yes, same type, same resolve |
| 10 | Also accept Odoo's compact string (`"today -30d =1d"`) as written sugar for URLs and typed filters? | not now; the structured node is the wire, a string form can be added later without breaking it |
| 9 | Name of the TS namespace: `when` collides with `When` / `readonly_when` / `step.when` | `dynamic` (`dynamic.today()`, `dynamic.context("user_id")`) |
| 8 | `period` with `unit` + `last`/`next` (rolling) beside `name` (calendar): both, or only `name`? | both; a rolling window is the most common saved filter |

## 11. Phases

1. **Split, no behaviour change.** Files as in §8; `walk.py` replaces the seven walkers; the
   vectors file starts with today's behaviour.
2. **Resolve.** `values.py`, `resolve.py`, `ResolutionContext`, `today` / `now` / `period` /
   `context` / `param`, the repository boundary, the cache key over the resolved tree, tests with
   a pinned clock.
3. **One language.** `presentation` and workflows adopt `Expression`; `Is`/`AllHold`/`AnyHolds`
   and `TEXT` go; `field`.
4. **Publish.** `resolvers` in `Meta`, JSON Schema, `@sincpro/criteria` `when.*`, the resolver
   registry for projects.

## 12. The TypeScript side (`@sincpro/criteria`)

Typed by hand where ergonomics matter (`Fields<T>`, `NoInfer`), checked against the generated
contract so nothing can drift: a test asserts that every `"$"` name, unit, period and operator in
`criteria.schema.json` equals the constants the package exports (the same shape as the existing
«covers every operator» test).

```ts
export const UNITS = ["minutes", "hours", "days", "weeks", "months", "years"] as const;
export type Unit = (typeof UNITS)[number];
export type Span = { [K in Unit]?: number };

export type Dynamic =
  | { $: "today" | "now"; plus?: Span; start_of?: Unit }
  | { $: "period"; name: PeriodName; plus?: Span }
  | { $: "context"; key: string }
  | { $: "param"; name: string }
  | { $: "field"; name: string };

export type Value = string | number | boolean | null | Date | Value[] | Dynamic;
```

`Value` has no plain-object member today, so a `{"$": …}` object cannot be mistaken for a literal.
`GRAINS` becomes a subset of `UNITS`. The authoring side:

```ts
["due", "<", when.today()]
["created_at", ">=", when.today({ plus: { days: -30 } })]
["owner_id", "=", when.context("user_id")]
when.period("this_mont")            // compile error: not a PeriodName
when.today({ plus: { day: -3 } })   // compile error: not a Unit
```

| File | Change |
|---|---|
| `criteria/grammar.ts` | `Unit`, `Span`, `PeriodName`, `Dynamic`; `Value` takes `Dynamic`; `Grain ⊂ Unit` |
| `criteria/when.ts` (new) | the `when` namespace beside `where` and `measure` |
| `engine/resolve.ts` (new) | `resolve(expression, context)`, built on `engine/temporal.ts` and `engine/grain.ts` |
| `engine/evaluate.ts` | `matches` / `filtered` resolve **once per filter**, never per row |
| `validate.ts` | a resolver's output type against `FieldMeta`; an unknown `context` key is a `Refusal` |
| `meta/meta.ts` | `Meta.resolvers`, so a filter builder offers them without being told twice |
| `explain.ts` | reads a dynamic value in words («last 30 days») |
| `criteria/pack.ts`, `merge.ts` | nothing: a dynamic value is plain JSON, so a packed URL and a saved view keep `today` |
| `criteria.schema.json` | generated, not written |
| `tests/parity.test.ts` | reads the extended vectors, copied by `make criteria-contract` |
| `@sincpro/criteria-react` | the filter builder lists `Meta.resolvers` |

## 13. Technical risks to prove in phase 1

1. **Pydantic union.** `Condition.value` is `JsonValue | datetime | …`, and `JsonValue` already
   accepts any dict, so `{"$": "today"}` may validate as a plain dict before `Dynamic` is tried.
   A `before` validator or a callable discriminator on the `"$"` key, plus a test that the
   round trip `model_dump` → `model_validate` keeps the node.
2. **The discriminator is a `Literal`**, and the project rule is `StrEnum` for closed sets: the
   resolver names are a `StrEnum` and each node's tag is `Literal[Resolvers.TODAY]`, so the set
   stays one exported enum.
3. **`resolve` once, not per row** in memory, and once per query in SQL.
4. **A timezone other than UTC against a datetime column**: the local date is right, but the date's
   midnight is taken in UTC. The field type is not known to `resolve`; `Meta` would have to convert.

## 14. What was built (backend, 2026-10-12)

Phases 1 and 2 of §11, in `sincpro_framework/ddd/criteria/`. The suite passes (3249 tests, 49 new in
`tests/ddd/criteria/test_resolve.py`, 6 over SQLite, 3 over the cache key).

| Piece | Where |
|---|---|
| The split | `expression.py` (tree, operators, `TEXT`), `sort.py`, `grouping.py`, `criteria.py` (`Criteria`, `Specification` and `Bucket` stay together: they refer to each other), `strict.py` gains `refused_or_dropped` |
| One rebuild | `walk.py`: `mapped(expression, leaf)` replaces the three hand-written `match` blocks of `with_text`, `Meta._pruned` and the resolver |
| The nodes | `values.py`: `Today`, `Now`, `FromContext`, `Param`, `Period`; `Span`, `Grain`, `Periods`, `ValueKind`; `before` / `after` / `at_start_of` |
| Reading them | `resolve.py`: `resolve`, `ResolutionContext` (one instant), stdlib only |
| Where it runs | `Meta.accept` resolves first, so the memory store, SQL, scopes and the cache fingerprint see literals; `fingerprint_of` takes an optional `resolution` |

Differences from the plan above:

- The context node is `FromContext(key=…)` (the wire name stays `context`): `Context` already means the
  framework's own context. The tag enum is `ValueKind`, not `Resolver`, which `ddd.relations` owns.
- **Public names**: `ddd.criteria` and `ddd` export only what a caller writes (`Today`, `Now`,
  `FromContext`, `Period`, `Periods`, `Grain`; `Param` only from `ddd.criteria`). `resolve`,
  `ResolutionContext`, `mapped`, `Span`, `ValueKind` are the framework's own: whoever needs them imports
  the file.
- The pydantic union risk of §13 is solved: a callable discriminator on the `"$"` key; a literal
  object without it stays a literal, and an unknown name is refused naming the known ones.

## 15. Layers (2026-10-12)

`ddd/criteria/` is `domain` ← `services` ← `entrypoint`, and its `__init__` exposes the domain and the
entrypoints, never `services/`. `tests/ddd/criteria/test_layers.py` refuses a layer that imports one
above it, and a file outside the package that imports one of its layers.

| Layer | Holds |
|---|---|
| `domain/` | the language and what is intrinsic to it: `Criteria`, `Specification`, `Bucket`, `Condition`/`All`/`Any_`/`Not`, `Sort`, `Grouping`, `Pagination`, the values read when used and `ResolutionContext`; the pure operations on the tree: `combined`, `conditions_of`, `mapped`, `canonical`, `with_text` |
| `services/` | what needs more than the language: `calendar` (move and snap a day, the range of a period), `reading` (one dynamic value), `comparison` (one operator, the way SQL answers it) |
| `entrypoint/` | what other modules call: `resolution` (`resolve`, `has_dynamic_values`) and `evaluation` (`holds`, `matches`) |

Two entrypoints and not one: each is a capability, as `cache.py` and `query_caching.py` are in
`data_layer/caching`. `Operator` stays in `domain/expression.py` with the tree it types.

## 16. Finished in Python (2026-10-12)

| Piece | Where |
|---|---|
| `field`: a column against a column, `{"$": "field", "name": …}` (`FromField`) | read per row by `holds`, a column in `clause_over`, validated by `Meta` (it exists, same type, comparing operator) or dropped and reported |
| Rolling `period`: `unit` with `last` / `next` and `include_current` | `services/calendar.py`; `Period` is a name or a window, never both |
| `Level.grain` is `Grain`, with the quarter (`2026-Q3`) | SQLite, PostgreSQL and DuckDB translators and `bucket_range` |
| A date against an instant column is its midnight in the context's timezone | `FieldType.read(raw, timezone)`; `Meta.accept` takes the zone from the resolution or the context |
| `Meta.resolvers`: name, what it reads to, parameters and choices | `domain/values.py` `RESOLVERS` |
| The contract: `criteria.schema.json` generated from the models, `criteria-resolution.json` with hand-written expectations, `make criteria-contract` | `tests/ddd/criteria/test_criteria_contract.py`; a test fails when what is committed is not what the engine generates |

What is left is the TypeScript side (§12) and, in Python, nothing: `Operator` stays with the tree it types.
