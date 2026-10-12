"""What a grouping needs to list its groups: the numbers a date part takes, every value of an axis
that `fill` completes, and what a measure is over a group with no rows.

`Level(fill=True)` lists every value a level can take so a group with no rows is still a group.
Pure: the dates and the choices come in as arguments, the groups come out as values.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from sincpro_framework.ddd.criteria.domain.criteria import Criteria
from sincpro_framework.ddd.criteria.domain.expression import Expression
from sincpro_framework.ddd.criteria.domain.grouping import Grouping, Level, Measure
from sincpro_framework.ddd.criteria.domain.values import PART_VALUES, Part
from sincpro_framework.ddd.criteria.services.axes import ONE, bounds_of, label_of
from sincpro_framework.ddd.criteria.services.calendar import on_calendar, snapped
from sincpro_framework.ddd.exceptions import InvalidCriteria


def empty_measure(measure: Measure) -> Any:
    """What a function folds over no rows: a count is 0, anything else has no value.

    in      Measure(count, id)        →  out  0
    in      Measure(sum, total)       →  out  None
    """
    return 0 if measure.function in ("count", "count_distinct") else None


def part_of(value: Any, part: Part) -> int | None:
    """The number a date part is: what a database extracts from the column.

    in      2026-03-14 15:09, Part.MONTH         →  out  3
    in      2026-03-14, Part.DAY_OF_WEEK         →  out  6        a Saturday; Monday is 1
    in      None                                  →  out  None
    """
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    match part:
        case Part.YEAR:
            return value.year
        case Part.QUARTER:
            return (value.month - 1) // 3 + 1
        case Part.MONTH:
            return value.month
        case Part.ISO_WEEK:
            return value.isocalendar()[1]
        case Part.DAY_OF_WEEK:
            return value.isoweekday()
        case Part.DAY_OF_MONTH:
            return value.day
        case Part.HOUR:
            return value.hour if isinstance(value, datetime) else 0


def axis_values(
    level: Level,
    choices: Sequence[Any],
    boolean: bool,
    where: Expression | None,
) -> list[Any]:
    """Every value a level can take, in ascending order.

        in      Level(status, fill), choices ['open', 'paid', 'overdue']      →  all three
        in      Level(made_at, part=month, fill)                              →  1 … 12
        in      Level(made_at, grain=month, fill), where made_at in Q1        →  '2026-01' … '2026-03'

    A date grain needs the filter to bound the field at both ends — an axis with no ends has no
    «every» — and a year part needs it as well. Anything else with no known set of values is
    refused, naming what to do.
    """
    if level.part is not None:
        known = PART_VALUES[level.part]
        if known is not None:
            return list(range(known[0], known[1] + 1))
        span = bounds_of(where, level.field)
        if span is None:
            raise InvalidCriteria(
                f"'{level.field}' filled by year needs the filter to bound it at both ends"
            )
        return list(range(span[0].year, (span[1] - timedelta(microseconds=1)).year + 1))

    if level.grain is not None:
        span = bounds_of(where, level.field)
        if span is None:
            raise InvalidCriteria(
                f"'{level.field}' filled by {level.grain.value} needs the filter to bound it at "
                "both ends: the groups of an axis with no ends cannot be listed"
            )
        labels: list[str] = []
        cursor = snapped(span[0].date(), level.grain, 0)
        last = (span[1] - timedelta(microseconds=1)).date()
        while cursor <= last:
            labels.append(label_of(cursor, level.grain))
            cursor = on_calendar(cursor, ONE[level.grain])
        return labels

    if boolean:
        return [False, True]
    if choices:
        return list(choices)
    raise InvalidCriteria(
        f"'{level.field}' cannot be filled: it has no choices to list. Fill an enum, a boolean, "
        "a date part, or a date grain that the filter bounds"
    )


def check_levels(criteria: Criteria, grouping: Grouping) -> None:
    """Refuses a grouping its levels cannot answer, naming why.

    - A **part** level cannot give a group its ids or a cursor: «every March» is not a condition
      on the date, so a page per group would list rows that are not the group's.
    - A **fill** level lists the groups with no rows, which a `where_measures` filter or a page of
      groups cannot place: both decide over the groups the rows made.
    """
    for level in grouping.group_by:
        if level.part is not None and criteria.pagination.asked:
            raise InvalidCriteria(
                f"'{level.field}' is grouped by a part of the date ({level.part.value}); a "
                "group of a part has no rows to page, because «every "
                f"{level.part.value}» is not a condition on the date"
            )
        if level.fill and (grouping.where_measures is not None or grouping.paged):
            raise InvalidCriteria(
                f"'{level.field}' is filled, and a filter or a page over the groups cannot "
                "place the groups that have no rows: drop `fill`, or the filter and the page"
            )


def check_measures(grouping: Grouping, types: Mapping[str, str]) -> None:
    """Refuses a fold the field's type cannot take: `bool_and` and `bool_or` fold a boolean.

    in      Measure(bool_and, settled), {'settled': 'boolean'}   →  accepted
    in      Measure(bool_and, total), {'total': 'number'}        →  InvalidCriteria
    """
    for measure in grouping.measures.values():
        if (
            measure.function in ("bool_and", "bool_or")
            and types.get(measure.field) != "boolean"
        ):
            raise InvalidCriteria(
                f"'{measure.function}' folds a boolean field; '{measure.field}' is a "
                f"{types.get(measure.field, 'field that does not exist')}"
            )
