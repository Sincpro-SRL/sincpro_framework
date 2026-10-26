"""`Evaluation`: the filter language answered in memory, over records already in hand."""

from collections.abc import Callable
from typing import Any

from sincpro_framework.ddd.criteria.domain.expression import Expression
from sincpro_framework.ddd.criteria.domain.resolution import ResolutionContext
from sincpro_framework.ddd.criteria.services import evaluation


class Evaluation:
    """Whether a filter holds, asked in memory by the same rules the ORM translates to SQL.

    Evaluation.matches(Thing(size=3), Condition(field="size", operator=Operator.GT, value=1))
        ->  True
    Evaluation.holds(Condition(field="size", operator=Operator.GT, value=1), {"size": 3}.get)
        ->  True
    """

    @staticmethod
    def holds(
        expression: Expression | None,
        read: Callable[[str], Any],
        resolution: ResolutionContext | None = None,
    ) -> bool:
        """Whether the filter holds over values `read` answers by field name — a column of a
        row, a path into a workflow's results. With a `resolution`, a dynamic value is read when
        its condition is reached and not before."""
        return evaluation.holds(expression, read, resolution)

    @staticmethod
    def matches(record: Any, expression: Expression | None) -> bool:
        """Whether this record answers the filter, its fields read as attributes."""
        return evaluation.matches(record, expression)
