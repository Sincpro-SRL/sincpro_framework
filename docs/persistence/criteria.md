# Criteria: the boundary language

`Criteria` is the one object that crosses a boundary. A screen sends it, a saved reading is one,
another bounded context receives one when a relation is resolved through its bus, and a Feature
hands it to the repository. It is JSON, it is typed, and every part of it is optional.

## The grammar

```json
{
  "selection":         {"all": [{"field": "pages", "operator": ">", "value": 200},
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
| `selection` | the filter: a `Condition`, `all` / `any` / `negate` of more, or `through` a relation | the operators a field takes come from its type and are published in `Meta`; a value written as text is read into the field's type |
| `order` | the ordering, `-` for descending | the identity is appended as tiebreaker so a keyset is a total order; a nullable column orders too: NULL first ascending, last descending, and the keyset places it the same way |
| `pagination` | how many and from where | `Cursor` (keyset, the default; the token is opaque and typed, it carries datetimes and decimals) or `Offset` (skip rows; costs grow with the offset) |
| `specification` | what to bring back of each record | field → criteria, recursive; see [specification.md](specification.md) |
| `grouping` | how to split the set when counting rather than listing | `group_by` the levels (a level's `grain` cuts a date: `day`, `week` ISO, `month`, `quarter` as `2026-Q3`, `year`; its `part` extracts a number from it; its `fill` lists the groups with no rows — see [analytics](analytics.md)), `measures` the folds (`sum avg min max count count_distinct percentile median bool_and bool_or`), `where_measures` a filter over what they folded, `order` by a level, a total or `count`, `pagination` for how many groups of the first level. One statement per level; drilling down one click at a time is opening a bucket with a grouping of its own. **With an explicit `pagination` on the criteria, a page per group**: every group of the deepest level carries the ids of its first `limit` rows, an exact count and a cursor; `search` with both answers `limit` rows for every group |
| `count` | `none`, `capped` (stops at 10 000 and says so), `exact` | free when the first page came back short |

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
| embedded, many2one, one2many, many2many | none: filter by the key, `author_id`, which is a scalar, or cross the relation with `through` |

`selection` filters the rows; `where_measures` filters the groups those rows made, and reads the names in
`measures` plus `count`. A name outside them is refused rather than dropped, because a filter
that vanished there would answer groups the caller ruled out.

## Crossing a relation: `through`

```json
{"selection": {"through": "lines", "selection": {"field": "qty", "operator": ">", "value": 10}}}
```

Some record the relation holds answers the inner selection: the orders with a line of more than
ten units. It composes like any other part — inside `all`, `any` and `negate`, and inside another
`through` — and its inner selection names the related record's fields, checked against the related
model; what that model cannot answer is dropped with its path (`lines.colour`).

| Relation | In SQL |
|---|---|
| a to-many by a foreign key | `EXISTS (SELECT 1 FROM line WHERE line.order_id = order.id AND …)` |
| a to-one | `EXISTS (SELECT 1 FROM customer WHERE customer.id = order.customer_id AND …)` |
| a many-to-many | `EXISTS` through its table in between |

An `EXISTS` either way, so a parent is never repeated. Inside it the relation reads as it always
does — its own scope, and the archived left out — so a filter never looks at a record the reading
would not bring. `Meta` publishes `navigable` on each relation: a relation the store keeps is
crossed; one a resolver, a bus or another service brings is not, and a filter through it is dropped
as `not_navigable`. Memory answers the same over the records it holds. A relation named in the
`specification` shapes what each record brings — `limit: 0` brings none of it — and never which
records come: that is `through`.

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
| `selection` | AND | both restrictions hold |
| `specification` | intersection | a mask can only take away; that is what makes it safe as a permission |
| `order`, `pagination`, `grouping`, `count` | the more specific replaces | "twenty per page" and "fifty per page" have no combination |
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

From JSON: `Criteria.model_validate(payload)`. In Python: the nodes directly, `Criteria(selection=
All(all=[Condition(field=…, operator=Operator.GT, value=…)]), order=Sort.parse("-pages"))`.
The nodes carry the names the JSON uses — `All`, `Any`, `Not` and `Condition`, from
`sincpro_framework.ddd` — so `{"any": [...]}` is `Any(any=[...])`.
`Query` is the command base that carries one, `ResponsePaginatedQuery` the answer base that
returns the page with its definition cut by the same specification. A client pages by the answer's
`cursor`, sent back in the `Query`'s own `cursor` field — never by rewriting the criteria's
`pagination` (see [paging](../entrypoints/fastapi.md#paging-one-cursor-sent-back-as-it-came)).

## A page over a statement you wrote

A join, a subquery or a report is not a table the framework maps, so a criteria cannot name its
fields as model fields. `search_sql` takes the statement and reads the criteria over its columns:
the filter, the ordering, the cursor and the count are the same as for `search`.

```python
statement = text(
    "SELECT t.thing_id, t.name, t.size, t.tags, t.made_at, t.owner, l.label "
    "FROM thing t JOIN thing_label l ON l.thing_id = t.thing_id"
).columns(*thing_table.c, column("label", Text))

page = repository.search_sql(Thing, statement, Criteria(selection=..., pagination=Pagination(limit=20)))
```

- The rows come back as the model: the statement names every column the model maps, under its
  name, and a statement missing one is refused.
- A report has no model to read as: `query_page(statement, criteria, identity="thing_id")` returns a
  `RowPage` of dicts, the cursor and the count. Its ordering ends on `identity`, so a cursor names one row.
- Every value a criteria carries is bound as a parameter. A filter over a relation is refused: a
  statement has no relation to cross.
- The statement must be one read: a write (`UPDATE`, `DELETE`, `INSERT`), a row lock (`FOR UPDATE`)
  or a second statement after `;` is refused with `ProgrammingError`. The statement itself is still
  written by the server: the framework binds values, it cannot tell a fixed statement from one built
  from a request, so that stays the developer's rule.

### Exposing it to a client

A `Query` answered from a statement is a Feature that declares the statement, and nothing on the
wire carries SQL. The Query has only `criteria` and `cursor`; the Feature holds the statement and
its permission:

```python
@bus.feature(QueryLabelledThings)
@auth.requires(Labels.READ)
class ListLabelledThings(Feature):
    STATEMENT = text("SELECT … FROM thing t JOIN thing_label l …").columns(...)

    def execute(self, dto: QueryLabelledThings) -> ResponseLabelledThings:
        page = self.repository.search_sql(Thing, self.STATEMENT, dto.criteria)
        return ResponseLabelledThings(things=..., cursor=page.cursor)
```

Use it for the resources a plain `search` cannot express — a join to another entity, a projection
that crosses tables — and not as a generic capability. The statement must filter what the model's
own reads filter (archived records, tenant), because `search_sql` does not add it. The columns a
statement returns beyond the model's are filterable but not hydrated onto the entity: a UI that
shows them reads them with `query_page`, or the model gains the field.

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
