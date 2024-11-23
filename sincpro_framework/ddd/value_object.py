"""Value object module."""

from typing import Callable, NewType, Type, TypeVar

AllPrimitiveType = int | float | str | bool | list | dict | set | tuple | bytes | bytearray

PrimitiveType = TypeVar(
    "PrimitiveType", int, float, str, bool, list, dict, set, tuple, bytes, bytearray
)


def new_value_object(
    new_type: Type[AllPrimitiveType] | Type[NewType],
    validate_fn: Callable[[AllPrimitiveType], None] | None,
) -> Type[AllPrimitiveType]:
    """Create a new value object."""
    name = new_type.__name__
    base_type = new_type.__supertype__

    class ValueObjectType(base_type):
        def __new__(cls, value: AllPrimitiveType) -> AllPrimitiveType:
            if validate_fn:
                validate_fn(value)
            return super().__new__(cls, value)

        def __repr__(self):
            return f"{name}({super().__repr__()})"

    ValueObjectType.__name__ = name
    ValueObjectType.__qualname__ = name
    ValueObjectType.__module__ = new_type.__module__

    return ValueObjectType
