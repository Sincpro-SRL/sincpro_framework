"""`Predicates`: a predicate written as Python over an entity, read once into the language.

expression_of(Sale, lambda s: s.total > 100 and s.state != SaleState.VOID)
"""

import ast
from collections.abc import Callable
from typing import Any

from sincpro_framework.ddd.criteria.domain.expression import Expression
from sincpro_framework.ddd.criteria.services import predicates


def expression_of[T](entity: type[T], predicate: Callable[[T], bool]) -> Expression:
    """The expression `predicate` says about a record of `entity`, read from its syntax tree —
    typed by `entity`, so a type checker reads each field and value against it."""
    return predicates.expression_of(entity, predicate)


class Predicates:
    """What reads a lambda's syntax tree, for the framework's own readers of a declaration — a
    form hint's `When(i.state != …, …)` among them."""

    @staticmethod
    def lambda_of(function: Callable[..., Any]) -> tuple[ast.Lambda, str]:
        """The syntax tree of `function`'s own lambda, and where it is written."""
        return predicates.lambda_of(function)

    @staticmethod
    def of_a_value(predicate: Callable[[Any], bool]) -> Expression:
        """The expression `predicate` says about one value, read as the field `value`."""
        return predicates.value_expression_of(predicate)

    @staticmethod
    def read_inside(function: Callable[..., Any], node: ast.expr) -> Expression:
        """The expression a part of `function`'s lambda says, its parameter as the record."""
        return predicates.read_inside(function, node)
