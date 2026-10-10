# Criteria grammar

```json
{
  "where":         {"all": [{"field": "pages", "operator": ">", "value": 200},
                            {"any": [{"field": "kind", "operator": "in", "value": ["book", "paper"]},
                                     {"negate": {"field": "title", "operator": "like", "value": "draft"}}]}]},
  "order":         [{"field": "pages", "descending": true}, {"field": "title"}],
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
| `where` | a `Condition` or `all`/`any`/`negate` of more | also accepted as a JSON string (how it survives a URL) |
| `order` | a list of `{"field", "descending"}` | the identity is appended as tiebreaker; nullable fields are refused |
| `pagination` | `limit` (default 50) and `strategy` | `{"token": …}` is a `Cursor` (default), `{"rows": n}` an `Offset` |
| `specification` | field → criteria, recursive | what to bring back; relations named here are resolved |
| `grouping` | `group_by`, `measures`, `where_measures`, `order`, `pagination` | used by `group_by_levels`, and by `search` for "a page per group" |
| `count` | `none`, `capped` (default), `exact` | |
| `meta` | whether the model definition travels | `true` by default |

Unknown keys are refused: `op`, `desc`, a top-level `limit` or `grouping.by` raise a
`ValidationError` naming them, so a typo never silently falls back to a default. Use the names
above exactly. A caller that must accept extra keys (a link written against an older schema)
reads with `Criteria.model_validate(value, context=TOLERANT)` (`from sincpro_framework.ddd.criteria import TOLERANT`), which leaves them out.

## In Python

The nodes carry the names the JSON uses, from `sincpro_framework.ddd`: `Condition`, `All`, `Any`,
`Not`.

```python
from sincpro_framework.ddd import All, Condition, Criteria, Operator, Pagination
from sincpro_framework.ddd.criteria import parse_order

Criteria(
    where=All(all=[
        Condition(field="state", value="draft"),                       # operator defaults to "="
        Condition(field="total", operator=Operator.GT, value=100),
    ]),
    order=parse_order("-total,number"),
    pagination=Pagination(limit=20),
)
```

`parse_order("-total,number")` turns URL-style ordering into `(Sort(total, descending), Sort(number))`.
It is a Python helper: the JSON `order` is always the list form. `Condition.value` is required;
`between` takes `[low, high]`, `in`/`not in` take a list, `is null` takes `true`/`false`.

## Operators by type

| Type (from the annotation) | Operators |
|---|---|
| `str` | `=` `!=` `like` `in` `not in` |
| `int` | `=` `!=` `<` `<=` `>` `>=` `between` `in` `not in` |
| `float`, `Decimal`, `date`, `datetime` | `=` `!=` `<` `<=` `>` `>=` `between` |
| `bool` | `=` |
| `uuid.UUID` | `=` `!=` `in` `not in` |
| `list[str]` | `contains` `not contains` `=` `!=` |
| `Translated` (`TranslatedText` column) | `like` (across every language) |
| any `… \| None` field | `is null` as well |
| embedded, many2one, one2many, many2many | none: filter by the key (`author_id`), a scalar |

`page.meta` publishes exactly these per field (`ops`), so a client builds its filter form from the
answer. A value written as text is read into the field's type (`"1000"` → `1000`).

## `dropped`: never a 400 — for `where` and `specification`

```json
"dropped": [{"field": "legacy_flag", "reason": "unknown_field"},
            {"field": "made_at",     "reason": "unsupported_operator"},
            {"field": "row_count",   "reason": "bad_value"},
            {"field": "title",       "reason": "not_expandable"}]
```

A criteria lives in URLs and saved readings, which outlive the schema they were written for, so an
unanswerable condition is removed and reported and the request still runs. A dropped filter always
*widens* the result: tell the client, and never rely on a server-side condition you have not seen
survive. Scopes that must hold go through `repository.narrowed(...)`, which refuses instead.

What is **raised** (`InvalidCriteria`, kind `domain`: every gateway answers 422, do not remap it by hand): ordering by an unknown or unorderable
field, an unknown measure function, a `where_measures` name outside the measures, a grouping by
nothing, a `percentile` on SQLite.

## How two criterias merge

`mine.merged_with(theirs)` is total, so two readings never disagree quietly:

| Part | Law |
|---|---|
| `where` | AND — both restrictions hold |
| `specification` | intersection — a mask can only take away |
| `order` | `theirs` when it set one, else `mine` |
| `pagination` | `theirs` when it differs from the default, else `mine` with `theirs`' strategy |
| `grouping` | `theirs` when it groups, else `mine` |
| `count` | `theirs` |
| `meta` | AND — otherwise `meta=false` could never be said |
| cursor | never carried over from `mine`: it belongs to one ordering over one filter |

The axis rule for building a filter from query parameters: `?origin=a&origin=b&runs=none` means
`origin IN (a, b) AND runs = none`. Repeated parameter = OR within one field; different parameters
= AND. Do not build a set algebra.

## Query and ResponsePaginatedQuery

`Query` is the Command base: one field, `criteria: Criteria = Criteria()`.
`ResponsePaginatedQuery` is the answer base: `cursor`, `count`, `entity_meta_data`, `dropped`, plus
**exactly one** field of the subclass's own holding the records (two raise `ContractViolation`).
`ResponseX.of(page, criteria)` fills it and applies the specification on the wire: each record shows
the named fields plus its identity, a to-one as an object or `null`, a to-many as
`{"items", "count", "cursor"}`. Inside the process the records stay full typed aggregates.

The same pair is what the other side of a cross-context relation (`Relation.bus`) implements.
