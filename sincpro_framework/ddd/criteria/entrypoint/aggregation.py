"""`Aggregation`: what an engine that groups rows asks besides the grouping itself."""

from collections.abc import Sequence
from typing import Any

from sincpro_framework.ddd.criteria.domain.expression import Expression
from sincpro_framework.ddd.criteria.domain.grouping import GroupingLevel
from sincpro_framework.ddd.criteria.services import axes


class Aggregation:
    """The pieces both engines — SQL and memory — share to answer a grouping the same way.

        every_month = GroupingLevel(field="made_at", part=Part.MONTH, fill=True)
        Aggregation.axis_values(every_month, (), False, None)    ->  [1, 2, …, 12]

    What a grouping checks of itself is its own (`Grouping.check_levels`, `check_measures`), what
    a measure is over no rows is the measure's (`Measure.over_no_rows`), and the number a date
    part is, the part's (`Part.of`).
    """

    @staticmethod
    def axis_values(
        level: GroupingLevel,
        choices: Sequence[Any],
        boolean: bool,
        where: Expression | None,
    ) -> list[Any]:
        """Every value a level can take, in ascending order — what `fill` completes an axis
        with, so a group with no rows is still a group."""
        return axes.axis_values(level, choices, boolean, where)
