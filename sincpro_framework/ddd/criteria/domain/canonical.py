"""The one form of a filter: the same rows asked for in another order are the same filter.

        in      All([b = 1, a in [3, 1]]) and All([a in [1, 3], b = 1])
        out     the same tree for both

What a filter means does not depend on the order of the parts of an `all` or an `any`, on the
order of the items of an `in`, or on how many zeros a decimal was written with. Whatever has to
tell two filters apart — the key of a cache — compares this form and not what was written.
"""

import json
from decimal import Decimal
from typing import Any

from sincpro_framework.ddd.criteria.domain.expression import (
    All,
    Any_,
    Condition,
    Expression,
    Not,
)


def _value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return value.normalize()
    if isinstance(value, list):
        return sorted(
            (_value(one) for one in value), key=lambda one: json.dumps(one, default=str)
        )
    return value


def _in_order(parts: Any) -> list[Any]:
    return sorted(
        parts, key=lambda part: json.dumps(part.model_dump(mode="json"), sort_keys=True)
    )


def _arranged(expression: Expression) -> Expression:
    match expression:
        case Condition():
            return expression.model_copy(update={"value": _value(expression.value)})
        case All():
            return All(all=_in_order(_arranged(part) for part in expression.all))
        case Any_():
            return Any_(any=_in_order(_arranged(part) for part in expression.any))
        case Not():
            return Not(negate=_arranged(expression.negate))


def canonical(expression: Expression | None) -> Expression | None:
    """The filter with its parts in a fixed order and its values in one spelling.

    >>> canonical(Condition(field="amount", value=Decimal("1.00")))
    Condition(field='amount', value=Decimal('1'), operator=<Operator.EQ: '='>)
    """
    return None if expression is None else _arranged(expression)
