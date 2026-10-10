"""Column types that map what a table holds to what the domain works with.

Two, and both are JSON in a text column: a list or mapping as the domain holds it, and a
text in several languages. A project with its own storage conventions declares its own
decorators beside these.
"""

import json
from typing import Any

from sqlalchemy import Dialect, Text, TypeDecorator


class JsonText(TypeDecorator):
    """A list or dict in Python, a JSON string in the column.

    write  ['age', 'sex']  →  '["age", "sex"]'
    read   '["age", "sex"]'  →  ['age', 'sex']
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        return None if value is None else json.dumps(value)

    def process_result_value(self, value: str | None, dialect: Dialect) -> Any:
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
        if value is None:
            return None
        if "default" not in value:
            raise ValueError("a translation needs a 'default' text")
        return json.dumps(dict(value), ensure_ascii=False)

    def process_result_value(
        self, value: str | None, dialect: Dialect
    ) -> dict[str, str] | None:
        return None if value is None else json.loads(value)
