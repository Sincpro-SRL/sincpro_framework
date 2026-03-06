"""Shared Value Object fixtures for DDD tests."""

from typing import NewType

from sincpro_framework.ddd import ValueObject
from sincpro_framework.ddd.value_object import new_value_object


def _validate_non_empty(value: str) -> str | None:
    if not value.strip():
        raise ValueError("Value cannot be empty")
    return None


# ---------------------------------------------------------------------------
# New API: ValueObject(base, fn, name=...)
# ---------------------------------------------------------------------------

UserIdVO = ValueObject(int, lambda v: abs(v), name="UserId")  # always positive
EmailVO = ValueObject(str, lambda v: v.strip().lower(), name="Email")
AmountVO = ValueObject(float, lambda v: round(v, 2), name="Amount")
TagVO = ValueObject(str, _validate_non_empty, name="Tag")
ProductCodeVO = ValueObject(str, lambda v: v.strip().upper(), name="ProductCode")
StockVO = ValueObject(int, lambda v: v * 2, name="Stock")  # doubles the value

# Collection primitives
TagsVO = ValueObject(list, lambda v: sorted(set(v)), name="Tags")  # dedup and sort
MetaVO = ValueObject(dict, lambda v: {k.strip(): val for k, val in v.items()}, name="Meta")
CoordSetVO = ValueObject(
    set, lambda v: {abs(x) for x in v}, name="CoordSet"
)  # abs of each element
PointVO = ValueObject(tuple, lambda v: tuple(sorted(v)), name="Point")  # sort elements

# ---------------------------------------------------------------------------
# Legacy API (deprecated): new_value_object(NewType(...), fn)
# ---------------------------------------------------------------------------

import warnings  # noqa: E402

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    LegacyUserIdVO = new_value_object(NewType("LegacyUserId", int), lambda v: abs(v))
    LegacyEmailVO = new_value_object(NewType("LegacyEmail", str), lambda v: v.strip().lower())
    LegacyAmountVO = new_value_object(NewType("LegacyAmount", float), lambda v: round(v, 2))
