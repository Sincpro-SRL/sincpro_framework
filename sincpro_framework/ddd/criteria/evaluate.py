"""The filter language answered in memory, over records already in hand.

The same `Expression` the adapter translates to SQL, evaluated with `getattr` and a
comparison. Two reasons it exists: a criteria can be tested without a database, and a
collection can narrow itself by the same filter a caller would send — `page.filtered_by(...)`.

**It agrees with SQL, including where SQL is odd.** A `NULL` compared with anything is neither
true nor false in SQL, so the row is excluded; here a `None` fails every operator except
`is null` and the empty-list question, which the translator also special-cases. The test that
runs one criteria through both evaluators and compares the sets is what keeps this true.

Values are expected as the field's own type. A condition that arrived as text from a URL goes
through `Meta.accept` first, which is what reads `"1000"` into `1000`.
"""

from typing import Any

from sincpro_framework.ddd.criteria.criteria import (
    All,
    Any_,
    Condition,
    Expression,
    Not,
    Operator,
)
from sincpro_framework.ddd.exceptions import InvalidCriteria


def _is_empty_list(value: Any) -> bool:
    return isinstance(value, list) and not value


def _holds(actual: Any, operator: Operator, value: Any) -> bool:
    """One comparison, the way the translator would have SQL answer it.

    in      3, GT, 1              →  True
    in      None, GT, 1           →  False        NULL compares to nothing
    in      None, IS_NULL, True   →  True
    in      [], EQ, []            →  True         the empty-list question, both spellings
    """
    if operator is Operator.IS_NULL:
        return (actual is None) if value else (actual is not None)

    if operator is Operator.EQ and _is_empty_list(value):
        return actual is None or actual == []
    if operator is Operator.NE and _is_empty_list(value):
        return actual is not None and actual != []

    if actual is None:
        return False

    match operator:
        case Operator.EQ:
            return actual == value
        case Operator.NE:
            return actual != value
        case Operator.IN:
            return actual in value
        case Operator.NOT_IN:
            return actual not in value
        case Operator.GT:
            return actual > value
        case Operator.GTE:
            return actual >= value
        case Operator.LT:
            return actual < value
        case Operator.LTE:
            return actual <= value
        case Operator.BETWEEN:
            return value[0] <= actual <= value[1]
        case Operator.LIKE:
            return str(value).lower() in str(actual).lower()
        case Operator.CONTAINS:
            return value in actual
        case Operator.NOT_CONTAINS:
            return value not in actual
        case _:
            raise InvalidCriteria(f"no in-memory evaluation for operator '{operator}'")


def matches(record: Any, expression: Expression | None) -> bool:
    """Whether this record answers the filter.

        in      Thing(size=3), Condition(size, GT, 1)                    →  True
        in      Thing(size=3), All([size > 1, Not(size == 3)])          →  False
        in      anything, None                                          →  True   no filter

    >>> matches(thing, criteria.expression)
    True
    """
    match expression:
        case None:
            return True
        case Condition():
            return _holds(
                getattr(record, expression.field), expression.operator, expression.value
            )
        case All():
            return all(matches(record, part) for part in expression.all)
        case Any_():
            return any(matches(record, part) for part in expression.any)
        case Not():
            return not matches(record, expression.negate)
    raise InvalidCriteria(f"not a filter expression: {expression!r}")
