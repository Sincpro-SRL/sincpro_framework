# Criteria grammar

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

## In Python

The nodes carry the names the JSON uses, from `sincpro_framework.ddd`: `Condition`, `All`, `Any`,
`Not`.

```python
from sincpro_framework.ddd import All, Condition, Criteria, Operator, parse_order

Criteria(
    where=All(all=[
        Condition(field="state", value="draft"),
        Condition(field="total", operator=Operator.GT, value=100),
    ]),
    order=parse_order("-total,number"),
)
```

`parse_order("-total,number")` turns URL-style ordering into the list form (the identity is appended
as a tiebreaker so a keyset is a total order).

## Operators by type

| Type | Operators |
|---|---|
| text | `=` `!=` `like` `in` `not in` |
| integer | `=` `!=` `<` `<=` `>` `>=` `between` `in` `not in` |
| number, date, datetime | `=` `!=` `<` `<=` `>` `>=` `between` |
| boolean | `=` |
| text[] | `contains` `not contains` `=` `!=` |
| translated | `like` |
| any nullable field | `is null` as well |
| embedded, many2one, one2many, many2many | none: filter by the key (`author_id`), which is a scalar |

A value written as text is read into the field's type. A condition the model cannot answer is not an
error — it is dropped and reported with a reason.

## `dropped`: never a 400

```json
"dropped": [{"field": "legacy_flag", "reason": "unknown_field"},
            {"field": "made_at",     "reason": "unsupported_operator"},
            {"field": "row_count",   "reason": "bad_value"},
            {"field": "title",       "reason": "not_expandable"}]
```

A dropped filter always *widens*. A criteria lives in URLs and saved readings, and those outlive the
schema they were written for — failing hard kills the link; dropping silently is worse.

## How two criterias merge

`mine.merged_with(theirs)` is total, so two readings never disagree quietly:

| Part | Law | Why |
|---|---|---|
| `where` | AND | both restrictions hold |
| `specification` | intersection | a mask can only take away |
| `order`, `pagination`, `grouping`, `count` | the more specific replaces | no combination for scalar choices |
| `meta` | AND | otherwise `meta=false` could never be said |
| cursor | never carried over | it belongs to one ordering over one filter |

The axis rule falls out of query-parameter semantics for free: `?origin=a&origin=b&runs=none` means
`origin IN (a, b) AND runs = none`. Repeated parameter = OR within one field; different parameters =
AND. Do not build a set algebra.

## Query and ResponsePaginatedQuery

`Query` is the command base that carries a Criteria; `ResponsePaginatedQuery` the answer base that
returns the page cut by the same specification. Use them when a relation is resolved through a bus.
