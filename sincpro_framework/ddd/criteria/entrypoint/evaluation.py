"""The filter language answered in memory, over records already in hand.

The same `Expression` the adapter translates to SQL, evaluated with `getattr` and a comparison. Two
reasons it exists: a criteria can be tested without a database, and a collection can narrow itself
by the same filter a caller would send — `page.filtered_by(...)`.

**It agrees with SQL, including where SQL is odd.** A `NULL` compared with anything is neither true
nor false in SQL, so the row is excluded; here a `None` fails every operator except `is null` and
the empty-list question, which the translator also special-cases. The test that runs one criteria
through both evaluators and compares the sets is what keeps this true.

Values are expected as the field's own type. A condition that arrived as text from a URL goes
through `Meta.accept` first, which is what reads `"1000"` into `1000`.
"""

from collections.abc import Callable
from functools import partial
from typing import Any

from sincpro_framework.ddd import exceptions
from sincpro_framework.ddd.criteria.domain.expression import (
    All,
    Any_,
    Condition,
    Expression,
    Not,
)
from sincpro_framework.ddd.criteria.domain.resolution import ResolutionContext
from sincpro_framework.ddd.criteria.domain.values import FromField
from sincpro_framework.ddd.criteria.entrypoint.resolution import has_dynamic_values, resolve
from sincpro_framework.ddd.criteria.services.comparison import compare


def holds(
    expression: Expression | None,
    read: Callable[[str], Any],
    resolution: ResolutionContext | None = None,
) -> bool:
    """Whether the filter holds over values `read` answers by field name — a column of a row,
    a path into a workflow's results.

        in      {"size": 3}.get, Condition(size, GT, 1)                  →  True
        in      anything, None                                          →  True   no filter

    With a `resolution`, a dynamic value is read when its condition is reached and not before,
    so `all` and `any` still stop at the first part that decides and a value that is out of
    reach is never asked for.
    """
    match expression:
        case None:
            return True
        case Condition():
            if resolution is not None and has_dynamic_values(expression):
                return holds(resolve(expression, resolution), read)
            value = expression.value
            if isinstance(value, FromField):
                value = read(value.name)
                if value is None:
                    return False
            return compare(read(expression.field), expression.operator, value)
        case All():
            return all(holds(part, read, resolution) for part in expression.all)
        case Any_():
            return any(holds(part, read, resolution) for part in expression.any)
        case Not():
            return not holds(expression.negate, read, resolution)
    raise exceptions.InvalidCriteria(f"not a filter expression: {expression!r}")


def matches(record: Any, expression: Expression | None) -> bool:
    """Whether this record answers the filter.

        in      Thing(size=3), Condition(size, GT, 1)                    →  True
        in      Thing(size=3), All([size > 1, Not(size == 3)])          →  False
        in      anything, None                                          →  True   no filter

    >>> matches(thing, criteria.expression)
    True
    """
    return holds(expression, partial(getattr, record))
