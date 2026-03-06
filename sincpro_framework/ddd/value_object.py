"""Value object module."""

from typing import Any, Callable, TypeVar, cast

T = TypeVar("T")


def _build_value_object_type(
    base: type, name: str, validate_fn: Callable[[Any], Any] | None, module: str
) -> type:
    """Internal factory that creates the ValueObjectType class."""

    class _ValueObjectType(base):  # type: ignore[valid-type,misc]
        def __new__(cls, value: Any) -> Any:
            if validate_fn:
                result = validate_fn(value)
                if result is not None:
                    value = result
            return super().__new__(cls, value)

        def __repr__(self) -> str:
            return f"{name}({super().__repr__()})"

        @classmethod
        def __get_pydantic_core_schema__(cls, source_type: Any, handler: Any) -> Any:
            from pydantic_core import core_schema as cs

            return cs.chain_schema(
                [
                    handler.generate_schema(base),
                    cs.no_info_plain_validator_function(cls),
                ]
            )

    _ValueObjectType.__name__ = name
    _ValueObjectType.__qualname__ = name
    _ValueObjectType.__module__ = module

    return _ValueObjectType


def ValueObject(
    base: type[T],
    validate_fn: Callable[[Any], Any] | None = None,
    name: str | None = None,
) -> type[T]:
    """Create a value object type from a primitive base type.

    Acts as a middleware factory: the validate_fn intercepts construction
    and can transform or validate the value before the object is created.

    Args:
        base: The primitive type to subclass (int, str, float, etc.).
        validate_fn: Optional function to validate/transform the value.
            If it returns a non-None value, that value replaces the original.
        name: Optional name for the resulting type. Defaults to the base type name.

    Returns:
        A new class that is a subclass of base with the validation middleware applied.

    Example:
        UserId = ValueObject(int, lambda v: abs(v), name="UserId")
        user_id = UserId(-5)  # UserId(5)
    """
    _name = name or base.__name__
    return cast(type[T], _build_value_object_type(base, _name, validate_fn, base.__module__))


def new_value_object(
    new_type: Any,
    validate_fn: Callable[[Any], Any] | None,
) -> Any:
    """Create a value object from a NewType or a plain type (retro-compatible API).

    .. deprecated::
        ``new_value_object`` will be removed in the next major release.
        Use ``ValueObject`` instead::

            # Before
            UserId = NewType("UserId", int)
            UserIdVO = new_value_object(UserId, lambda v: abs(v))

            # After
            UserIdVO = ValueObject(int, lambda v: abs(v), name="UserId")

    Args:
        new_type: A NewType instance (has __supertype__) or a plain type.
        validate_fn: Optional function to validate/transform the value.

    Returns:
        A new class that is a subclass of the underlying primitive type.
    """
    import warnings

    warnings.warn(
        "new_value_object() is deprecated and will be removed in the next major release. "
        "Use ValueObject() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    if hasattr(new_type, "__supertype__"):
        # Called with NewType("X", int) — legacy API path
        base = new_type.__supertype__
        name = new_type.__name__
        module = new_type.__module__
    else:
        # Called with a plain type (int, str, ...) — delegate to ValueObject
        base = new_type
        name = new_type.__name__
        module = new_type.__module__

    return _build_value_object_type(base, name, validate_fn, module)
