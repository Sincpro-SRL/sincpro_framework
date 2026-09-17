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
  "grouping":      {"by": [{"field": "author_id"}, {"field": "printed_at", "grain": "month"}],
                    "totals": {"pages": ["sum", "pages"]}},
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
| `grouping` | how to split the set when counting rather than listing | `by` the levels, `totals` the folds, `having` a filter over what they folded, `order` by a level, a total or `count`, `pagination` for how many groups of the first level. One statement per level; drilling down one click at a time is opening a bucket with a grouping of its own. **With an explicit `pagination` on the criteria, a page per group**: every group of the deepest level carries the ids of its first `limit` rows, an exact count and a cursor; `search` with both answers `limit` rows for every group |
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

`where` filters the rows; `having` filters the groups those rows made, and reads the names in
`totals` plus `count`. A name outside them is refused rather than dropped, because a filter
that vanished there would answer groups the caller ruled out.

`matches(record, expression)` in `ddd/evaluate.py` is the in-memory evaluator and the
specification of the translator: every operator has to agree with it on every row, and the
real-world suite checks exactly that over the whole ledger.

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
All(all=[Condition(field=…, operator=Operator.GT, value=…)]), order=parse_order("-pages"))`.
`Query` is the command base that carries one, `ResponsePaginatedQuery` the answer base that
returns the page with its definition cut by the same specification.
