"""Column types that map what a table holds to what the domain works with.

Three, and all are JSON: a value of the type the domain declares — a model, a dataclass, a list
of them — in a JSON column; a list or mapping as the domain holds it, and a text in several
languages, in a text column. A project with its own storage conventions declares its own
decorators beside these.
"""

import json
from typing import Any

from pydantic import TypeAdapter
from sqlalchemy import JSON, Dialect, Text, TypeDecorator
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql.operators import OperatorType
from sqlalchemy.types import TypeEngine


class TypedJson[T](TypeDecorator[T]):
    """A value of the declared type in Python, JSON in the column — `jsonb` on Postgres.

        Column("address", TypedJson(Address))               a model, a dataclass, a TypedDict
        Column("lines", TypedJson(list[Line]), nullable=False)

        write  Address(city="La Paz")  →  {"city": "La Paz"}            validated on the way
        read   {"city": "La Paz"}      →  Address(city="La Paz")        the type, never a dict

    Whatever pydantic can validate is a type here: a `Decimal` is written as text and read back
    a `Decimal`, a `StrEnum` as its value, a date in ISO. `None` is SQL `NULL`, not a JSON `null`.

    **A value changed inside is written** — `invoice.address.city = "Sucre"` and a save — like
    any column of a `Database`'s session (`in_place_changes.py`).

    **Compared as JSON.** `where(Work.size == Shape(15, 21))` writes the value as it is stored;
    `Work.size["width"]` reaches into it as a JSON column does. A migration writes the column
    as the store's own type — `JSONB` on Postgres, `JSON` elsewhere — never this one: a step
    does not import the project.
    """

    impl = JSON(none_as_null=True)
    cache_ok = True

    def __init__(self, shape: Any) -> None:
        """`shape` is any type pydantic validates; its `TypeAdapter` is built once, here, not
        per row."""
        super().__init__()
        self.shape = shape
        self.adapter: TypeAdapter[T] = TypeAdapter(shape)

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        """`jsonb` on Postgres: compared, indexed and stored parsed, where `json` is only text."""
        if dialect.name == "postgresql":
            return JSONB(none_as_null=True)
        return self.impl_instance

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        """Validated against the shape, then dumped as JSON-ready data — a wrong value fails
        on the way in, never on the next read.

            Address(city="La Paz")   ->  {"city": "La Paz"}
            {"city": "La Paz"}       ->  {"city": "La Paz"}     a dict is validated too
        """
        if value is None:
            return None
        return self.adapter.dump_python(self.adapter.validate_python(value), mode="json")

    def process_result_value(self, value: Any, dialect: Dialect) -> T | None:
        """The stored JSON validated back into the declared type — never a bare dict."""
        return None if value is None else self.adapter.validate_python(value)

    def coerce_compared_value(self, op: OperatorType | None, value: Any) -> TypeEngine[Any]:
        """A value of the declared type is compared as this column writes it; anything else —
        a key, an index — as the JSON column does."""
        try:
            self.adapter.validate_python(value)
        except ValueError:
            return self.impl_instance.coerce_compared_value(op, value)
        return self


class JsonText(TypeDecorator):
    """A list or dict in Python, a JSON string in the column.

    write  ['age', 'sex']  →  '["age", "sex"]'
    read   '["age", "sex"]'  →  ['age', 'sex']
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        """The list or dict as a JSON string; `None` stays SQL `NULL`."""
        return None if value is None else json.dumps(value)

    def process_result_value(self, value: str | None, dialect: Dialect) -> Any:
        """The JSON string parsed back to the list or dict it was."""
        return None if value is None else json.loads(value)


class TranslatedText(TypeDecorator):
    """A text in several languages — `dict[str, str]` with a `default` — as a JSON object in
    the column.

        write  {"default": "Hola", "en": "Hello"}  →  '{"default": "Hola", "en": "Hello"}'
        read   '{"default": "Hola", "en": "Hello"}'  →  {"default": "Hola", "en": "Hello"}

    Written with the characters as they are, not escaped, so `like` over the stored text
    finds "año" when somebody types "año". A stored value without a default is refused on
    the way in: it would be a text nobody could read.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(
        self, value: dict[str, str] | None, dialect: Dialect
    ) -> str | None:
        """The translations as a JSON object, characters unescaped — refused without a
        `default`, the text every reader falls back to.

            {"default": "Año", "en": "Year"}  ->  '{"default": "Año", "en": "Year"}'
            None                              ->  None, SQL NULL
            {"en": "Year"}    ->  ValueError("a translation needs a 'default' text")

        The mapping is copied into a plain `dict` first, so any `Mapping` is written as one.
        """
        if value is None:
            return None
        if "default" not in value:
            raise ValueError("a translation needs a 'default' text")
        return json.dumps(dict(value), ensure_ascii=False)

    def process_result_value(
        self, value: str | None, dialect: Dialect
    ) -> dict[str, str] | None:
        """The stored JSON object back as `{language: text}`."""
        return None if value is None else json.loads(value)
