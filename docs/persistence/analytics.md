# Analytics from the model: computed fields, parts of a date, groups with no rows

Define the entity and its table, and group, filter, order and total by things that are **not
columns** — a balance, a status, the month of the year — without writing the SQL of a query.
The same criteria answers the same numbers in a memory store (what a project tests against) and
in SQL (what it runs on); `tests/data_layer/orm/test_model_driven_analytics.py` holds both to
one set of rows.

## A computed field

```python
from dataclasses import dataclass
from decimal import Decimal

from sincpro_framework.ddd import Entity, computed


@dataclass
class Invoice(Entity):
    total: Decimal = Decimal("0")
    paid: Decimal = Decimal("0")
    balance: Decimal = computed(
        sql=lambda t: t.total - t.paid,
        python=lambda self: self.total - self.paid,
        default=Decimal("0"),
    )
```

`balance` is not a column and is never written. `sql` is an expression over the table's columns
(`t.total`) that SQLAlchemy runs; `python` is the same value over a record, which is what a
memory store answers. `map_aggregates` maps the `sql` as a `column_property`, so the field is read
with the row and is filtered, ordered and grouped like a column:

```python
from sincpro_framework.ddd import Condition, Criteria, Operator
from sincpro_framework.ddd.criteria import Grouping, Level, Measure

owing = Criteria(where=Condition(field="balance", operator=Operator.GT, value=0))

by_owner = Criteria(
    grouping=Grouping(
        group_by=(Level(field="owner"),),
        measures={"owing": Measure(function="sum", field="balance")},
    )
)
```

What it is, exactly:

| | |
|---|---|
| Declared | `computed(sql=…, python=…, default=…)` on the dataclass field; at least one of `sql` and `python` |
| Not an `__init__` argument | and a store ignores a value written to it |
| Published | `Meta.fields["balance"]` with `computed=True`, `readonly=True`, `groupable=True` and the `aggregates` its type takes |
| `sql` | receives the table's columns; a column the table lacks is refused when `map_aggregates` runs, naming the field |
| `python` | optional. Without it a memory store **refuses** to filter, order or group by the field, instead of guessing a value that quietly differs from SQL |
| A record not yet read from a store | holds the `default` (and `None` once the class is mapped): the value exists after a read |
| Cost | the expression is part of every `SELECT` of the entity. A subquery over another table is allowed and costs what it costs |

**The two implementations have to agree.** `ComputedFieldsContract`, in
`sincpro_framework.runtime.testing.contracts.computed_fields`, puts both in front of the same rows:

```python
from sincpro_framework.runtime.testing.contracts.computed_fields import ComputedFieldsContract


class TestInvoiceBalance(ComputedFieldsContract):
    entity = Invoice

    def make_store(self):
        ...  # a store holding some invoices
```

## The part of a date

A **grain** cuts a date into ranges (`2026-03`); a **part** extracts the number (`3`), so every
March groups together whatever the year:

```python
from sincpro_framework.ddd.criteria import Level, Part

Level(field="posted_at", part=Part.MONTH)         # 1 … 12
Level(field="posted_at", part=Part.DAY_OF_WEEK)   # 1 (Monday) … 7
```

`Part` is `YEAR`, `QUARTER`, `MONTH`, `ISO_WEEK`, `DAY_OF_WEEK`, `DAY_OF_MONTH`, `HOUR`. One level
takes a grain or a part, not both. A group of a part has **no filter that opens it** («every
March» is not a condition on the date): `Bucket.openable` is `False`, its `criteria` is its
parent's, and asking for a page of ids per group over a part is refused. Another engine is taught
its words with `register_part_translator(dialect, translator)`, as `register_grain_translator`
does for grains. The quarter is also a grain (`2026-Q3`).

## Groups with no rows

A group that happens to be empty is silently absent from a grouping, and a chart with a hole in its
axis lies. `Level(fill=True)` lists every value the level can take, with a count of 0:

```python
Level(field="state", fill=True)                      # every choice of the enum
Level(field="settled", fill=True)                    # False and True
Level(field="posted_at", part=Part.MONTH, fill=True) # 1 … 12
Level(field="posted_at", grain=Grain.MONTH, fill=True)  # every month the filter spans
```

An empty group's count is 0, its `count` and `count_distinct` folds are 0 and every other fold is
`None`. A date **grain** needs the filter to bound the field at both ends (`>=`/`<`, or
`between`): an axis with no ends has no «every», and it is refused saying so. A level with no
choices to list cannot be filled. A filled level is completed under every parent it has, and
cannot be combined with `where_measures` or a page of groups, which decide over groups the rows
made. Under a chosen `order` the empty groups follow the others.

## More folds

`median` (the 0.5 percentile, no argument), `bool_and` and `bool_or` (booleans only). `Meta`
publishes which functions each field takes (`FieldMeta.aggregates`), so a client offers exactly
those. SQLite has no percentile, so `median` is refused there, by name; a memory store answers it.

## Not here, on purpose

Computed over a **relation** (a count or sum of children), named measures and dimensions on the
entity, totals in one statement (`GROUPING SETS`), window measures (running total, rank) and
grouping in the context's timezone are not built: see [the specification](../spec/persistence.md)
for where each stands.
