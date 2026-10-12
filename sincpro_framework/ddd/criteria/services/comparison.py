"""One comparison, the way the translator would have SQL answer it."""

from typing import Any

from sincpro_framework.ddd.criteria.domain.expression import Operator
from sincpro_framework.ddd.exceptions import InvalidCriteria


def _is_empty_list(value: Any) -> bool:
    return isinstance(value, list) and not value


def compare(actual: Any, operator: Operator, value: Any) -> bool:
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
        case Operator.STARTS_WITH:
            return str(actual).lower().startswith(str(value).lower())
        case Operator.CONTAINS:
            return value in actual
        case Operator.NOT_CONTAINS:
            return value not in actual
        case _:
            raise InvalidCriteria(f"no in-memory evaluation for operator '{operator}'")
