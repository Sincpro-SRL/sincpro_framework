"""A field the entity answers by a computation the database can run, declared beside the field.

    @dataclass
    class Invoice(Entity):
        total: Decimal = Decimal("0")
        paid: Decimal = Decimal("0")
        balance: Decimal = computed(
            sql=lambda t: t.total - t.paid,
            python=lambda self: self.total - self.paid,
            default=Decimal("0"),
        )

`balance` is not a column, and it is not written: it is an expression over the row, so it can
be filtered, ordered and grouped by like one — `Level(field="balance")`, `Condition(field=
"balance", operator=Operator.GT, value=0)` — without the project writing the SQL of a query.

Two implementations of one value, on purpose: `sql` is what a database runs and `python` is what
a store held in memory answers, and a record loaded from the database carries the value `sql`
computed. They have to agree, and `ComputedFieldsContract` is the test that says they do. Without
`python` a memory store refuses to filter or group by the field instead of guessing it.

`sql` receives the table's columns (`t.total`) and answers a SQLAlchemy expression. It may use
anything SQLAlchemy offers, including a correlated subquery over another table.
"""

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

COMPUTED = "computed"
"""The key under which a dataclass field's metadata carries its `Computation`."""


@dataclass(frozen=True)
class Computation:
    """How a computed field is answered: by the database, by memory, or by both."""

    sql: Callable[[Any], Any] | None = None
    python: Callable[[Any], Any] | None = None


def computed(
    sql: Callable[[Any], Any] | None = None,
    python: Callable[[Any], Any] | None = None,
    default: Any = None,
) -> Any:
    """A dataclass field the entity computes rather than stores.

    `sql` is the expression over the table's columns; `python` is the same value over a record
    (`lambda self: …` or `Invoice.balance_of`); `default` is what a record that was never loaded
    holds. The field is read-only: it is not an `__init__` argument, and a store ignores a value
    written to it.

    >>> computed(sql=lambda t: t.total - t.paid, default=0)
    """
    if sql is None and python is None:
        raise TypeError("a computed field is computed by `sql`, by `python`, or by both")
    return dataclasses.field(
        default=default, init=False, metadata={COMPUTED: Computation(sql=sql, python=python)}
    )


def computations_of(declared: type) -> dict[str, Computation]:
    """The computed fields of a class and how each is answered; empty when there are none.

    in      Invoice (balance computed)    →  out  {'balance': Computation(sql=…, python=…)}
    in      a class that is not a dataclass   →  out  {}
    """
    if not dataclasses.is_dataclass(declared):
        return {}
    return {
        one.name: one.metadata[COMPUTED]
        for one in dataclasses.fields(declared)
        if COMPUTED in one.metadata
    }
