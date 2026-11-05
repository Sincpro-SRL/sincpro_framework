"""`ValueObject`: a primitive with a name and a rule — an `int` that is a `UserId`, a `str`
that is an `Email` — still the primitive everywhere it goes.

    UserId = ValueObject(int, lambda v: abs(v), name="UserId")
    UserId(-5)                     ->  UserId(5)        an int: UserId(5) + 1 == 6

    Email = ValueObject(str, check_email, name="Email")
    class CommandInvite(DataTransferObject):
        email: Email                pydantic validates as str, then runs check_email

    Discount = ValueObject(Decimal, name="Discount", holds=lambda v: 0 <= v <= 30)
    Discount(45)                   ->  ValueError: Discount holds `0 <= v <= 30`; 45 does not

Context: a subclass of the primitive, not a wrapper, so a store, a JSON encoder or an `==`
sees the plain value and nothing has to unwrap it. The rule runs on every construction — by
hand or through pydantic — so a value of the type has always passed it.

Two kinds of rule, beside each other. `validate_fn` is Python: it checks or transforms, and only
the server can run it. `holds` is what the value holds, written as Python over it and read once
into an expression of the language: the server checks it on every construction, and it is
published — as JSON Schema keywords where one says the same (`minimum`, `maximum`, `enum`), and
whole under `x-sincpro` — so a client checks it too, offline.
"""

import ast
from decimal import Decimal
from typing import Any, Callable, TypeVar, cast

from sincpro_framework.ddd.criteria import (
    All,
    Condition,
    Evaluation,
    Expression,
    Operator,
    Predicates,
)

T = TypeVar("T")

HOLDS = "__sincpro_holds__"
"""The attribute a value object's class carries its `holds` expression under."""

BOUNDS = {
    Operator.GTE: "minimum",
    Operator.GT: "exclusiveMinimum",
    Operator.LTE: "maximum",
    Operator.LT: "exclusiveMaximum",
}
"""The comparisons JSON Schema says with a keyword of its own."""


def keywords_of(expression: Expression) -> dict[str, Any]:
    """The JSON Schema keywords that say what `expression` says of a value, when they say all of
    it; empty when some part has no keyword — the expression travels whole beside them anyway.

        value >= 0 and value <= 30      ->  {"minimum": 0, "maximum": 30}
        value in ["a", "b"]             ->  {"enum": ["a", "b"]}
        value != 0                      ->  {}
    """
    parts = expression.all if isinstance(expression, All) else [expression]
    said: dict[str, Any] = {}
    for part in parts:
        if not isinstance(part, Condition) or part.field != "value":
            return {}
        bound = part.value
        if (
            part.operator in BOUNDS
            and isinstance(bound, (int, float, Decimal))
            and not isinstance(bound, bool)
        ):
            said[BOUNDS[part.operator]] = (
                float(bound) if isinstance(bound, Decimal) else bound
            )
        elif part.operator is Operator.IN and isinstance(part.value, list):
            said["enum"] = list(part.value)
        else:
            return {}
    return said


def _build_value_object_type(
    base: type,
    name: str,
    validate_fn: Callable[[Any], Any] | None,
    module: str,
    holds: Expression | None,
    written: str,
) -> type:
    """The class behind `ValueObject`: a subclass of `base` whose
    construction runs `validate_fn`, then checks `holds`, named `name` and placed in `module`.

    1. `__new__` runs the rule; a result that is not `None` replaces the value — a rule that
       only checks raises and returns nothing.
    2. Then what the value holds is checked by its compiled program, refused naming the rule as
       it was written: the Python rule may have normalised the value, and the final one is judged.
    3. Pydantic validates the base type first, then builds this class from it (both rules run).
    4. Its JSON schema is the base's, titled with the value object's name, with the keywords that
       say what it holds and the expression itself under `x-sincpro`.
    Final: named so a traceback and a schema say `UserId`, not `_ValueObjectType`.
    """
    program = Evaluation.program(holds) if holds is not None else None

    class _ValueObjectType(base):  # type: ignore[valid-type,misc]
        """A `base` that passed `validate_fn` and holds `holds` — renamed below."""

        def __new__(cls, value: Any) -> Any:
            """The value through the rule, checked against what it holds, then built as the
            primitive it is."""
            if validate_fn:
                result = validate_fn(value)
                if result is not None:
                    value = result
            if program is not None and not program(lambda field: value):
                raise ValueError(f"{name} holds `{written}`; {value!r} does not")
            return super().__new__(cls, value)

        def __repr__(self) -> str:
            """`UserId(5)`, `Email('a@b.c')` — the name around the primitive's own repr."""
            return f"{name}({super().__repr__()})"

        @classmethod
        def __get_pydantic_core_schema__(cls, source_type: Any, handler: Any) -> Any:
            """Validated as `base` first — `"5"` for an int is refused or coerced the way
            pydantic does — then constructed as this class, so the rules run."""
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
            """The base type's schema, titled with the value object's name, and what it holds."""
            json_schema = handler(core_schema)
            json_schema["title"] = name
            if holds is not None:
                json_schema.update(keywords_of(holds))
                json_schema["x-sincpro"] = {"holds": holds.model_dump(mode="json")}
            return json_schema

    _ValueObjectType.__name__ = name
    _ValueObjectType.__qualname__ = name
    _ValueObjectType.__module__ = module
    setattr(_ValueObjectType, HOLDS, holds)

    return _ValueObjectType


def holds_of(annotation: Any) -> Expression | None:
    """What a value object type holds, as an expression over `value`; `None` for any other
    type — what a field typed with it publishes."""
    return getattr(annotation, HOLDS, None) if isinstance(annotation, type) else None


def ValueObject(
    base: type[T],
    validate_fn: Callable[[Any], Any] | None = None,
    name: str | None = None,
    holds: Callable[[T], bool] | None = None,
) -> type[T]:
    """A value object type over a primitive: a subclass of `base` whose every construction
    goes through `validate_fn` and holds `holds`.

        UserId = ValueObject(int, lambda v: abs(v), name="UserId")
        UserId(-5)   ->  UserId(5)
        Percent = ValueObject(Decimal, name="Percent", holds=lambda v: 0 <= v <= 100)
        Percent(120) ->  ValueError: Percent holds `0 <= v <= 100`; 120 does not

    `validate_fn` checks or transforms: it raises to refuse, returns a value to replace the
    one given, or returns `None` to leave it as it is. `holds` is what the value holds, written
    as Python over it (`lambda v: …`) and read once into an expression: checked after
    `validate_fn`, published for a client to check offline. Without a `name` the type is called
    as its base.
    """
    _name = name or base.__name__
    expression = Predicates.of_a_value(holds) if holds is not None else None
    written = ast.unparse(Predicates.lambda_of(holds)[0].body) if holds is not None else ""
    return cast(
        type[T],
        _build_value_object_type(
            base, _name, validate_fn, base.__module__, expression, written
        ),
    )
