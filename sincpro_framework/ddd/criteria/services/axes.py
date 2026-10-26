"""What a grouping needs to list its groups: the range a filter holds a date field to, and every
value of an axis that `fill` completes.

`GroupingLevel(fill=True)` lists every value a level can take so a group with no rows is still a
group. Pure: the dates and the choices come in as arguments, the groups come out as values.
"""

from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from typing import Any

from sincpro_framework.ddd import exceptions
from sincpro_framework.ddd.criteria.domain.expression import (
    All,
    Condition,
    Expression,
    Operator,
)
from sincpro_framework.ddd.criteria.domain.grouping import GroupingLevel
from sincpro_framework.ddd.criteria.domain.values import PART_VALUES


def _as_datetime(value: Any) -> datetime:
    """A bound of a date range as a naive datetime, whatever it was written as.

        date(2026, 1, 1)              ->  datetime(2026, 1, 1, 0, 0)
        "2026-01-01T10:00:00+00:00"   ->  datetime(2026, 1, 1, 10, 0)      zone dropped
        42                            ->  InvalidCriteria

    Context: the axis is counted in calendar steps, where a zone only gets in the way of
    comparing one bound with another.
    """
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    if isinstance(value, str):
        return datetime.fromisoformat(value).replace(tzinfo=None)
    raise exceptions.InvalidCriteria(f"a date range is bounded by dates; got {value!r}")


def bounds_of(expression: Expression | None, field: str) -> tuple[datetime, datetime] | None:
    """The range a filter holds a date field to, `[low, high)`, when it bounds both ends.

        in      All([made_at >= 2026-01-01, made_at < 2026-04-01]), "made_at"
        out     (2026-01-01, 2026-04-01)

    Only the conditions that every row must meet count — the parts of an `all` — and `between`
    is closed at both ends, so its upper bound is nudged to the instant after it. A filter that
    leaves either end open does not bound the field.

    1. The parts: an `All`'s, or the expression alone.
    2. Each condition on `field`, in order — a later one overwriting an earlier bound:
       2.1 `>=` or `>` sets the low end (a `>` is taken as `>=`);
       2.2 `<` sets the high end; `<=` sets it to the instant after its value;
       2.3 `between` sets both, the high end the instant after its second value.
    Final: the pair when both ends were set — `None` otherwise.
    """
    parts = expression.all if isinstance(expression, All) else [expression]
    low: datetime | None = None
    high: datetime | None = None
    for part in parts:
        if not isinstance(part, Condition) or part.field != field:
            continue
        if part.operator in (Operator.GTE, Operator.GT):
            low = _as_datetime(part.value)
        elif part.operator is Operator.LT:
            high = _as_datetime(part.value)
        elif part.operator is Operator.LTE:
            high = _as_datetime(part.value) + timedelta(microseconds=1)
        elif part.operator is Operator.BETWEEN and isinstance(part.value, list):
            low, high = (
                _as_datetime(part.value[0]),
                _as_datetime(part.value[1]) + timedelta(microseconds=1),
            )
    return (low, high) if low is not None and high is not None else None


def axis_values(
    level: GroupingLevel,
    choices: Sequence[Any],
    boolean: bool,
    where: Expression | None,
) -> list[Any]:
    """Every value a level can take, in ascending order.

        in      GroupingLevel(status, fill), choices ['open', 'paid', 'overdue']      →  all three
        in      GroupingLevel(made_at, part=month, fill)                              →  1 … 12
        in      GroupingLevel(made_at, grain=month, fill), where made_at in Q1        →  '2026-01' … '2026-03'

    A date grain needs the filter to bound the field at both ends — an axis with no ends has no
    «every» — and a year part needs it as well. Anything else with no known set of values is
    refused, naming what to do.

    1. A part:
       1.1 one with a fixed range (`PART_VALUES`) — every number of it, first to last;
       1.2 a year — every year the filter's bounds on the field cover, the upper bound
           exclusive; no bounds is `InvalidCriteria`.
    2. A grain — no bounds is `InvalidCriteria`; else from the lower bound snapped to its
       grain, one label per grain up to the last instant before the upper bound.
    3. A boolean: `[False, True]`.
    4. Choices: the choices, in the order given.
    Final: anything else has nothing to list — `InvalidCriteria`, naming what can be filled.
    """
    if level.part is not None:
        known = PART_VALUES[level.part]
        if known is not None:
            return list(range(known[0], known[1] + 1))
        span = bounds_of(where, level.field)
        if span is None:
            raise exceptions.InvalidCriteria(
                f"'{level.field}' filled by year needs the filter to bound it at both ends"
            )
        return list(range(span[0].year, (span[1] - timedelta(microseconds=1)).year + 1))

    if level.grain is not None:
        span = bounds_of(where, level.field)
        if span is None:
            raise exceptions.InvalidCriteria(
                f"'{level.field}' filled by {level.grain.value} needs the filter to bound it at "
                "both ends: the groups of an axis with no ends cannot be listed"
            )
        labels: list[str] = []
        cursor = level.grain.snap(span[0].date())
        last = (span[1] - timedelta(microseconds=1)).date()
        while cursor <= last:
            labels.append(level.grain.label(cursor))
            cursor = level.grain.span.shift_on_calendar(cursor)
        return labels

    if boolean:
        return [False, True]
    if choices:
        return list(choices)
    raise exceptions.InvalidCriteria(
        f"'{level.field}' cannot be filled: it has no choices to list. Fill an enum, a boolean, "
        "a date part, or a date grain that the filter bounds"
    )
