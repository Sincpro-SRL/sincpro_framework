"""Shared Value Object fixtures for DDD tests."""

from typing import NewType

from sincpro_framework.ddd.value_object import new_value_object

_UserId = NewType("UserId", int)
_Email = NewType("Email", str)
_Amount = NewType("Amount", float)
_Tag = NewType("Tag", str)
_ProductCode = NewType("ProductCode", str)
_Stock = NewType("Stock", int)


def _validate_non_empty(value: str) -> str | None:
    if not value.strip():
        raise ValueError("Value cannot be empty")
    return None


UserIdVO = new_value_object(_UserId, lambda v: abs(v))  # siempre positivo
EmailVO = new_value_object(_Email, lambda v: v.strip().lower())
AmountVO = new_value_object(_Amount, lambda v: round(v, 2))
TagVO = new_value_object(_Tag, _validate_non_empty)
ProductCodeVO = new_value_object(_ProductCode, lambda v: v.strip().upper())
StockVO = new_value_object(_Stock, lambda v: v * 2)  # duplica
