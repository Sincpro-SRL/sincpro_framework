# Criteria: the boundary language

`Criteria` is the one object that crosses a boundary. A screen sends it, a saved reading is one,
another bounded context receives one when a relation is resolved through its bus, and a Feature
hands it to the repository. It is JSON, it is typed, and every part of it is optional.

## The grammar

```json
{
  "where":         {"all": [{"field": "pages", "operator": ">", "value": 200},
                            {"any": [{"field": "kind", "operator": "in", "value": ["book", "paper"]},
                                     {"negate": {"field": "title", "operator": "like", "value": "draft"}}]}]},
  "order":         "-pages,title",
  "pagination":    {"limit": 20, "strategy": {"token": "eyJ…"}},
  "specification": {"title": {}, "author": {"specification": {"name": {}}}},
  "grouping":      {"group_by": [{"field": "author_id"}, {"field": "printed_at", "grain": "month"}],
                    "measures": {"pages": {"function": "sum", "field": "pages"}}},
  "count":         "capped",
  "meta":          true
}
```

| Part | What it says | Notes |
|---|---|---|
| `where` | the filter: a `Condition` or `all` / `any` / `negate` of more | the operators a field takes come from its type and are published in `Meta`; a value written as text is read into the field's type |
| `order` | the ordering, `-` for descending | the identity is appended as tiebreaker so a keyset is a total order; a nullable column is refused for ordering because a keyset over NULLs loses rows |
| `pagination` | how many and from where | `Cursor` (keyset, the default; the token is opaque and typed, it carries datetimes and decimals) or `Offset` (skip rows; costs grow with the offset) |
| `specification` | what to bring back of each record | field → criteria, recursive; see [specification.md](specification.md) |
| `grouping` | how to split the set when counting rather than listing | `group_by` the levels (a level's `grain` cuts a date: `day`, `week` ISO, `month`, `quarter` as `2026-Q3`, `year`; its `part` extracts a number from it; its `fill` lists the groups with no rows — see [analytics](analytics.md)), `measures` the folds (`sum avg min max count count_distinct percentile median bool_and bool_or`), `where_measures` a filter over what they folded, `order` by a level, a total or `count`, `pagination` for how many groups of the first level. One statement per level; drilling down one click at a time is opening a bucket with a grouping of its own. **With an explicit `pagination` on the criteria, a page per group**: every group of the deepest level carries the ids of its first `limit` rows, an exact count and a cursor; `search` with both answers `limit` rows for every group |
| `count` | `none`, `capped` (stops at 10 000 and says so), `exact` | free when the first page came back short |
| `meta` | whether the definition travels | on by default |

Operators by type:

| Type | Operators |
|---|---|
| text | `=` `!=` `like` `in` `not in` |
| integer | `=` `!=` `<` `<=` `>` `>=` `between` `in` `not in` |
| number, date, datetime | `=` `!=` `<` `<=` `>` `>=` `between` |
| boolean | `=` |
| text[] | `contains` `not contains` `=` `!=` |
| translated | `like` |
| any nullable field | `is null` as well |
| embedded, many2one, one2many, many2many | none: filter by the key, `author_id`, which is a scalar |

`where` filters the rows; `where_measures` filters the groups those rows made, and reads the names in
`measures` plus `count`. A name outside them is refused rather than dropped, because a filter
that vanished there would answer groups the caller ruled out.

`Evaluation.matches(record, expression)`, from `sincpro_framework.ddd.criteria`, is the in-memory evaluator and the
specification of the translator: every operator has to agree with it on every row, and the
real-world suite checks exactly that over the whole ledger.

## Values that are read when the filter is used

A condition's `value` is a literal, or an object with the reserved key `"$"` naming a value the
server reads when the filter runs:

```json
{"field": "due",        "operator": "<",  "value": {"$": "today"}}
{"field": "created_at", "operator": ">=", "value": {"$": "today", "plus": {"days": -30}}}
{"field": "posted_at",  "operator": ">=", "value": {"$": "today", "start_of": "month"}}
{"field": "closed_at",  "operator": "between", "value": {"$": "period", "name": "this_month"}}
{"field": "owner_id",   "operator": "=",  "value": {"$": "context", "key": "user_id"}}
{"field": "closed_at",  "operator": ">=", "value": {"$": "context", "key": "report_date",
                                                     "plus": {"months": -3}, "start_of": "month"}}
```

It is stored as written, so a saved view that says `today` means the day it is opened. The server
resolves it — the client never sends a processed date, and a client cannot name another user:
`context` reads the server's own `use_context()`.

| `$` | Reads | Notes |
|---|---|---|
| `today` | the current date | in the timezone the server's context names under `timezone` (an IANA name, `America/La_Paz`), UTC without one; `week_starts_on` (0 is Monday) sets the week |
| `now` | the current instant | UTC |
| `period` | a span of the calendar as `[start, end)` | either `name` (`today`, `yesterday`, `tomorrow`, `this_`/`last_`/`next_` + `week`, `month`, `quarter`, `year`) or a window of whole units: `unit` with `last` or `next` — `{"$": "period", "unit": "day", "last": 30}` is the 30 days before today, today left out unless `include_current`. Only with `between` |
| `context` | a key of the server's context | `key`; a key the context does not hold is refused, never ignored |
| `param` | a value the caller supplied for this call | `name`, or a path with dots |
| `field` | another field of the same row | `name`. A column against a column: `{"field": "total", "operator": ">", "value": {"$": "field", "name": "credit_limit"}}`. Not read before the row; takes `=` `!=` `<` `<=` `>` `>=` and a field of the same type, or the condition is dropped and reported |

`plus` (a signed `Span`: `{"days": -30}`) and `start_of` (`day`, `week`, `month`, `quarter`,
`year`) are allowed on every value that is a date: `today`, `now`, and what `context` or `param`
read. **`plus` is applied first, then `start_of`**: `{"$": "today", "plus": {"months": -1},
"start_of": "month"}` is the first day of last month. Months and years move by the calendar and
clamp at a month end (31 January plus a month is 28 February); days follow the wall clock of the
timezone, so a day across a clock change is still a day; hours and minutes are elapsed time and
only `now` takes them. A week starts on Monday unless the resolution says otherwise.

**One instant per resolution**: `today` and `now` in the same filter agree. Everything is UTC; a
timezone only decides where a calendar boundary falls. A date compared with an instant column is
that date's midnight in the context's timezone, as a UTC instant: in La Paz, `2026-10-12` against a
datetime column is `2026-10-12T04:00:00Z`.

In Python the nodes are classes, moved with `before`, `after` and `at_start_of`:

```python
from sincpro_framework.ddd.criteria import Condition, FromContext, Grain, Operator, Today

Condition(field="due", operator=Operator.LT, value=Today())
Condition(field="created_at", operator=Operator.GTE, value=Today().before(days=30))
Condition(field="closed_at", operator=Operator.GTE,
          value=FromContext(key="report_date").before(months=3).at_start_of(Grain.MONTH))
```

`Meta.accept` reads them before it checks anything, so the memory store, SQL and the fingerprint
of a cache see only literals: the key of a cached read is made from the rows asked for, and two
users asking for «mine» never share one. `Resolution.resolve(expression, ResolutionContext(now=…))`, from `sincpro_framework.ddd.criteria`, is the same
step with the clock in hand, which is how a test pins the day.

## What *reflexive* means

A criteria is reflexive in two senses, and both are what make it scale.

**The answer says what to ask next.** Every page carries `Meta`: fields with their types and
operators, choices for an enum, labels in every language, relations with what identifies them and,
once expanded, the definition of the other side. A client builds its filter form, its column menu
and its next request from the previous answer. It never carries a schema of its own, and it can
never ask for a field it was not shown.

**A criteria contains criterias of the same type.** `specification` maps a name to a `Criteria`,
whose `specification` maps names to criterias, and so on:

```
q(g(f(x)))     ≡     Criteria(specification={"runs": Criteria(specification={"tags": Criteria()})})
```

Each hop consumes and produces the same types, `Criteria` in and `EntityCollection` out, so any
resolver, SQL in the same database, a command on another context's bus, a function over HTTP,
is interchangeable and composable. The framework applies the chain: the page, then one node,
then the node's own nodes, without learning anything new at any level. The reflected criteria a
relation sends to the other side is built by one function, `ddd.relations._reflected`, which is
the chain rule written down: the node's criteria plus the parents' keys.

Where the chain holds and where it does not is in [relations.md](relations.md).

## How two criterias merge

`mine.merged_with(theirs)` is total, so two readings never disagree quietly:

| Part | Law | Why |
|---|---|---|
| `where` | AND | both restrictions hold |
| `specification` | intersection | a mask can only take away; that is what makes it safe as a permission |
| `order`, `pagination`, `grouping`, `count` | the more specific replaces | "twenty per page" and "fifty per page" have no combination |
| `meta` | AND | otherwise `meta=false` could never be said |
| cursor | never carried over | it belongs to one ordering over one filter |

`criteria.resuming_from(cursor)` is how a walk continues; `Repository.stream` does it for you.

## Strict about the language, tolerant about the model

Two kinds of unknown, read two ways — the line Elasticsearch draws too:

| Unknown | Example | Answer |
|---|---|---|
| a key the **language** does not have | `{"limit": 10}` at the top, `{"field", "op", "value"}`, `{"all": […], "any": […]}` | **refused** — a typo, which would otherwise answer wrong |
| a field the **model** does not have | `{"field": "legacy_flag", …}` from a saved reading | dropped and reported (below) |

A query read leniently turns a typo into wrong data: a misspelt filter key is dropped and *more*
rows come back (RFC 9413, "virtuous intolerance"; GraphQL, JSON:API query parameters and
Elasticsearch refuse it too). A client of another version that sends keys this one does not
know asks for it where it is read: `Criteria.model_validate(data, context=TOLERANT)`.

## `dropped`: never a 400

A condition on a field the model does not have, an operator its type does not take, a value that
will not read, a specification node that names nothing: each is removed and reported with a
reason, and the request still runs.

```json
"dropped": [{"field": "legacy_flag", "reason": "unknown_field"},
            {"field": "made_at",     "reason": "unsupported_operator"},
            {"field": "row_count",   "reason": "bad_value"},
            {"field": "title",       "reason": "not_expandable"}]
```

A dropped filter always *widens* the result, never narrows it, so telling the client is enough.
A criteria lives in URLs and saved readings, and those outlive the schema they were written for.

## Writing one

From JSON: `Criteria.model_validate(payload)`. In Python: the nodes directly, `Criteria(where=
All(all=[Condition(field=…, operator=Operator.GT, value=…)]), order=Sort.parse("-pages"))`.
The nodes carry the names the JSON uses — `All`, `Any`, `Not` and `Condition`, from
`sincpro_framework.ddd` — so `{"any": [...]}` is `Any(any=[...])`.
`Query` is the command base that carries one, `ResponsePaginatedQuery` the answer base that
returns the page with its definition cut by the same specification.

## What a client reads

`Meta.resolvers` lists the dynamic values the server reads — each one's name, what it reads to and
its parameters, with the values a parameter may take — so a filter builder offers «Today» and «This
month» from what the backend says and a value a project adds appears with no client release.

The language is also published as data: `tests/ddd/criteria/criteria.schema.json` (the JSON Schema of
a `Criteria`, generated from the models), `criteria-parity.json` (what a filter matches, per
operator) and `criteria-resolution.json` (what each dynamic value reads to, given the instant, the
timezone, the context and the parameters; the expectations are written by hand). `make
criteria-contract` regenerates the three and copies them to the TypeScript package, and a test fails
when what is committed is not what the engine generates.
