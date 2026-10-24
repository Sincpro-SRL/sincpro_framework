"""The pieces of an axis: the text a database writes for a grain, and the range a filter holds a
date field to. Pure: the dates come in as arguments."""

from datetime import date, datetime, time, timedelta
from typing import Any

from sincpro_framework.ddd import exceptions
from sincpro_framework.ddd.criteria.domain.expression import (
    All,
    Condition,
    Expression,
    Operator,
)
from sincpro_framework.ddd.criteria.domain.values import Grain, Span

ONE: dict[Grain, Span] = {
    Grain.DAY: Span(days=1),
    Grain.WEEK: Span(weeks=1),
    Grain.MONTH: Span(months=1),
    Grain.QUARTER: Span(months=3),
    Grain.YEAR: Span(years=1),
}


def label_of(start: date, grain: Grain) -> str:
    """The text a database writes for the grain a date falls in; `bucket_range` reads it back.

        label_of(date(2026, 3, 14), Grain.WEEK)       ->  '2026-W11'    # ISO year and week
        label_of(date(2026, 8, 2), Grain.QUARTER)     ->  '2026-Q3'
        label_of(date(2026, 10, 24), Grain.MONTH)     ->  '2026-10'
        label_of(date(2026, 10, 24), Grain.YEAR)      ->  '2026'
        label_of(date(2026, 10, 24), Grain.DAY)       ->  '2026-10-24'

    Context: the date is labelled as it is — snapping it to its grain is the caller's.
    """
    match grain:
        case Grain.YEAR:
            return f"{start.year}"
        case Grain.QUARTER:
            return f"{start.year}-Q{(start.month - 1) // 3 + 1}"
        case Grain.MONTH:
            return f"{start.year}-{start.month:02d}"
        case Grain.WEEK:
            year, week, _ = start.isocalendar()
            return f"{year}-W{week:02d}"
        case Grain.DAY:
            return start.isoformat()


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
