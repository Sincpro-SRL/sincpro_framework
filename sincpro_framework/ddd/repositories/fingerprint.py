"""The fingerprint of a read: one key for every read that answers the same rows, whatever page it
asks for — so what was read once is never read again.

Context: it is taken after the filter's values are read as their fields' types, so `"1000"` and
`1000` are one key; the parts of an `all` or an `any`, and the items of an `in`, are keyed in any
order; a decimal is keyed by its value, not by how many zeros it was written with. The order and
the mask are part of it — they change which rows a page holds and what each row carries — and
so is the scope a repository reads under, so two tenants never share a key. The pagination is
not: a later page is the same read, continued.
"""

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sincpro_framework.ddd.criteria import Criteria
from sincpro_framework.ddd.criteria.criteria import All, Any_, Condition, Expression, Not
from sincpro_framework.ddd.entity.entity_meta import describe_class


def _value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value.normalize())
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, list):
        return sorted((_value(one) for one in value), key=json.dumps)
    return value


def _canonical(expression: Expression | None) -> Any:
    match expression:
        case None:
            return None
        case Condition():
            return [expression.field, expression.operator.value, _value(expression.value)]
        case All():
            return {
                "all": sorted((_canonical(one) for one in expression.all), key=json.dumps)
            }
        case Any_():
            return {
                "any": sorted((_canonical(one) for one in expression.any), key=json.dumps)
            }
        case Not():
            return {"not": _canonical(expression.negate)}


def fingerprint_of(model: type, criteria: Criteria, scope: Criteria | None = None) -> str:
    meta = describe_class(model)
    where, _ = meta.accept(criteria.expression)
    scoped, _ = meta.accept(scope.expression) if scope is not None else (None, [])
    dumped = criteria.model_dump(mode="json")
    body = {
        "aggregate": f"{model.__module__}.{model.__qualname__}",
        "where": _canonical(where),
        "scope": _canonical(scoped),
        "order": dumped["order"],
        "specification": dumped["specification"],
    }
    text = json.dumps(body, sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:32]
