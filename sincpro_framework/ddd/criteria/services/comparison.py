"""One comparison, the way the translator would have SQL answer it."""

from typing import Any

from sincpro_framework.ddd import exceptions
from sincpro_framework.ddd.criteria.domain.expression import Operator


def _is_empty_list(value: Any) -> bool:
    """Whether a value is `[]` — what turns `= []` and `!= []` into the empty-list question,
    where a missing list (`None`) and an empty one count as the same."""
    return isinstance(value, list) and not value


def compare(actual: Any, operator: Operator, value: Any) -> bool:
    """One comparison, the way the translator would have SQL answer it.

        compare(3, Operator.GT, 1)                    ->  True
        compare(None, Operator.GT, 1)                 ->  False    # NULL compares to nothing
        compare(None, Operator.IS_NULL, True)         ->  True
        compare(None, Operator.EQ, [])                ->  True     # the empty-list question,
        compare([], Operator.EQ, [])                  ->  True     # both spellings
        compare("Sincpro SRL", Operator.LIKE, "pro")  ->  True     # contained, any case
        compare(5, Operator.BETWEEN, [1, 5])          ->  True     # closed at both ends

    1. `IS_NULL`: a true value asks for `None`, a false one for anything else.
    2. `EQ` / `NE` with `[]`: the empty-list question — `None` and `[]` are both empty.
    3. Any other operator over `None`: `False`, as SQL's `NULL` answers.
    4. The operator, in Python:
       4.1 `EQ`, `NE`, `GT`, `GTE`, `LT`, `LTE` — the comparison itself;
       4.2 `IN`, `NOT_IN` — the actual among the values or not;
       4.3 `BETWEEN` — between both values, inclusive;
       4.4 `LIKE` — the value inside the actual, `STARTS_WITH` — the actual begins with it,
           both as text and in any case;
       4.5 `CONTAINS`, `NOT_CONTAINS` — the value among the actual's members, or not.
    Final: an operator with none of those is `InvalidCriteria`.
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
        case Operator.STARTS_WITH:
            return str(actual).lower().startswith(str(value).lower())
        case Operator.CONTAINS:
            return value in actual
        case Operator.NOT_CONTAINS:
            return value not in actual
        case _:
            raise exceptions.InvalidCriteria(
                f"no in-memory evaluation for operator '{operator}'"
            )
