"""`Formulas`: what a `@compute` returns, written down when the language can say it — for the
metamodel, which publishes it, and for whoever checks a client against the server.

    Formulas.of_a_method(Line.amount_of)          ->  {"op": "*", "args": [qty, price]}
    Formulas.value_of(formula, line_as_read)      ->  Decimal("25.00")
"""

from collections.abc import Callable
from typing import Any

from sincpro_framework.ddd.criteria.services import formulas


class Formulas:
    """The formulas of the language: read from a method's body, answered over a record."""

    @staticmethod
    def of_a_method(method: Callable[..., Any]) -> Any:
        """The formula `method` returns; `None` when its body is not one the language says."""
        return formulas.formula_of(method)

    @staticmethod
    def value_of(formula: Any, read: Callable[[str], Any]) -> Any:
        """What `formula` answers over the record whose fields `read` gives."""
        return formulas.value_of(formula, read)
