"""`ValueObject`: a primitive with a name and a rule — an `int` that is a `UserId`, a `str`
that is an `Email` — still the primitive everywhere it goes.

    UserId = ValueObject(int, lambda v: abs(v), name="UserId")
    UserId(-5)                     ->  UserId(5)        an int: UserId(5) + 1 == 6

    Email = ValueObject(str, check_email, name="Email")
    class CommandInvite(DataTransferObject):
        email: Email                pydantic validates as str, then runs check_email

Context: a subclass of the primitive, not a wrapper, so a store, a JSON encoder or an `==`
sees the plain value and nothing has to unwrap it. The rule runs on every construction — by
hand or through pydantic — so a value of the type has always passed it.
"""

from typing import Any, Callable, TypeVar, cast

T = TypeVar("T")


def _build_value_object_type(
    base: type, name: str, validate_fn: Callable[[Any], Any] | None, module: str
) -> type:
    """The class behind `ValueObject` and `new_value_object`: a subclass of `base` whose
    construction runs `validate_fn`, named `name` and placed in `module`.

    1. `__new__` runs the rule; a result that is not `None` replaces the value — a rule that
       only checks raises and returns nothing.
    2. Pydantic validates the base type first, then builds this class from it (the rule runs).
    3. Its JSON schema is the base's, titled with the value object's name.
    Final: named so a traceback and a schema say `UserId`, not `_ValueObjectType`.
    """

    class _ValueObjectType(base):  # type: ignore[valid-type,misc]
        """A `base` that passed `validate_fn` — renamed to the value object's name below."""

        def __new__(cls, value: Any) -> Any:
            """The value through the rule, then built as the primitive it is."""
            if validate_fn:
                result = validate_fn(value)
                if result is not None:
                    value = result
            return super().__new__(cls, value)

        def __repr__(self) -> str:
            """`UserId(5)`, `Email('a@b.c')` — the name around the primitive's own repr."""
            return f"{name}({super().__repr__()})"

        @classmethod
        def __get_pydantic_core_schema__(cls, source_type: Any, handler: Any) -> Any:
            """Validated as `base` first — `"5"` for an int is refused or coerced the way
            pydantic does — then constructed as this class, so the rule runs."""
            from pydantic_core import core_schema

            return core_schema.chain_schema(
                [
                    handler.generate_schema(base),
                    core_schema.no_info_plain_validator_function(cls),
                ]
            )

        @classmethod
        def __get_pydantic_json_schema__(
            cls, core_schema: Any, handler: Any
        ) -> dict[str, Any]:
            """The base type's schema, titled with the value object's name."""
            json_schema = handler(core_schema)
            json_schema["title"] = name
            return json_schema

    _ValueObjectType.__name__ = name
    _ValueObjectType.__qualname__ = name
    _ValueObjectType.__module__ = module

    return _ValueObjectType


def ValueObject(
    base: type[T],
    validate_fn: Callable[[Any], Any] | None = None,
    name: str | None = None,
) -> type[T]:
    """A value object type over a primitive: a subclass of `base` whose every construction
    goes through `validate_fn`.

        UserId = ValueObject(int, lambda v: abs(v), name="UserId")
        UserId(-5)   ->  UserId(5)

    `validate_fn` checks or transforms: it raises to refuse, returns a value to replace the
    one given, or returns `None` to keep it. Without a `name` the type is called as its base.
    """
    _name = name or base.__name__
    return cast(type[T], _build_value_object_type(base, _name, validate_fn, base.__module__))


def new_value_object(
    new_type: Any,
    validate_fn: Callable[[Any], Any] | None,
) -> Any:
    """Deprecated — the older spelling of `ValueObject`, kept for the code already written
    with it; it warns and goes away in the next major release.

        UserIdVO = new_value_object(NewType("UserId", int), lambda v: abs(v))     before
        UserIdVO = ValueObject(int, lambda v: abs(v), name="UserId")              now

    1. A `DeprecationWarning`, pointing at the caller.
    2. A `NewType` gives its supertype as the base and its own name and module.
    3. A plain type is its own base, named as itself.
    Final: the same class `ValueObject` builds.
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
