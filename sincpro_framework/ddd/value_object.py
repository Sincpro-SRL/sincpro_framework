"""Value object module."""

from typing import Any, Callable, NewType, Type, TypeVar

PrimitiveType = TypeVar(
    "PrimitiveType", int, float, str, bool, list, dict, set, tuple, bytes, bytearray
)


def new_value_object(
    new_type: Type[PrimitiveType] | Type[NewType],
    validate_fn: Callable[[PrimitiveType], Any] | None,
) -> Type[PrimitiveType]:
    """Create a new value object."""
    name = new_type.__name__  # type: ignore[union-attr]
    base_type = new_type.__supertype__  # type: ignore[union-attr]

    class ValueObjectType(base_type):  # type: ignore[valid-type,misc]
        def __new__(cls, value: PrimitiveType) -> PrimitiveType:
            if validate_fn:
                new_value = validate_fn(value)
                if new_value:
                    value = new_value
            return super().__new__(cls, value)

        def __repr__(self):
            return f"{name}({super().__repr__()})"

        @classmethod
        def __get_pydantic_core_schema__(cls, source_type: Any, handler: Any) -> Any:
            from pydantic_core import core_schema as cs

            return cs.chain_schema(
                [
                    handler.generate_schema(base_type),
                    cs.no_info_plain_validator_function(cls),
                ]
            )

    ValueObjectType.__name__ = name
    ValueObjectType.__qualname__ = name
    ValueObjectType.__module__ = new_type.__module__

    return ValueObjectType
