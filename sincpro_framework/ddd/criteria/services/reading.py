"""Reading one dynamic value: what the context, the call's parameters or the clock hold, moved and
snapped as the value asks."""

from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any

from sincpro_framework.ddd import exceptions
from sincpro_framework.ddd.criteria.domain.resolution import ResolutionContext
from sincpro_framework.ddd.criteria.domain.values import (
    FromContext,
    Now,
    Param,
    Period,
    Today,
)


def _read(source: Mapping[Any, Any], path: str, what: str) -> Any:
    """A value by name, or by a path of names with dots; a refusal naming it when absent."""
    found: Any = source
    for step in path.split("."):
        if not isinstance(found, Mapping) or step not in found:
            raise exceptions.InvalidCriteria(f"{what} has no '{path}'")
        found = found[step]
    return found


def _date_read(given: Any, what: str) -> date | datetime:
    """A value that has to be a date: itself, or the ISO text a context may hold it as."""
    if isinstance(given, (date, datetime)):
        return given
    if isinstance(given, str):
        try:
            return (
                date.fromisoformat(given)
                if len(given) == 10
                else datetime.fromisoformat(given)
            )
        except ValueError:
            pass
    raise exceptions.InvalidCriteria(f"{what} is not a date: {given!r}")


def _shifted(
    value: Today | Now | FromContext | Param, anchor: Any, resolution: ResolutionContext
) -> Any:
    """What a value reads, moved by its `plus` and then snapped to its `start_of`.

        Today(plus=Span(days=-30), start_of="month"), anchor 2026-03-14  ->  2026-02-01

    1. Neither asked: the anchor as it is — a non-date value passes untouched.
    2. The anchor read as a date; a naive datetime is taken as UTC.
    3. `plus` moves it by the span.
    3.1 `start_of` snaps a datetime in the resolution's zone — "the start of the month" is
        the local one — and hands it back in UTC.
    3.2 A date is snapped as it is.
    Final: the moved, snapped value.
    """
    if value.plus is None and value.start_of is None:
        return anchor

    day = _date_read(anchor, "a value that is moved or snapped")
    if isinstance(day, datetime) and day.tzinfo is None:
        day = day.replace(tzinfo=UTC)

    if value.plus is not None:
        day = value.plus.shift(day, resolution.timezone)
    if value.start_of is not None:
        if isinstance(day, datetime):
            local = day.astimezone(resolution.timezone).replace(tzinfo=None)
            start = value.start_of.snap(local, resolution.week_starts_on)
            return start.replace(tzinfo=resolution.timezone).astimezone(UTC)
        return value.start_of.snap(day, resolution.week_starts_on)
    return day


def value_of(value: Today | Now | FromContext | Param, resolution: ResolutionContext) -> Any:
    """The literal a dynamic value stands for.

        # now 2026-10-24 15:00 UTC; context {"user": {"id": 7}};
        # params {"since": "2026-01-01"}
        value_of(Today(), resolution)                         ->  date(2026, 10, 24)
        value_of(Today(plus=Span(days=-30), start_of="month"), resolution)
            ->  date(2026, 9, 1)
        value_of(Now(), resolution)          ->  datetime(2026, 10, 24, 15, 0, tzinfo=UTC)
        value_of(FromContext(key="user.id"), resolution)      ->  7
        value_of(Param(name="since"), resolution)             ->  "2026-01-01"
        value_of(Param(name="nope"), resolution)
            ->  InvalidCriteria: the parameters has no 'nope'

    Context: today is the date in the resolution's timezone, now is the instant in UTC; what
    each reads is then moved and snapped as the value asks (`_shifted`).
    """
    match value:
        case Today():
            anchor: Any = resolution.now.astimezone(resolution.timezone).date()
        case Now():
            anchor = resolution.now.astimezone(UTC)
        case FromContext():
            anchor = _read(resolution.context, value.key, "the context")
        case Param():
            anchor = _read(resolution.params, value.name, "the parameters")
    return _shifted(value, anchor, resolution)


def item_of(value: Any, field: str, resolution: ResolutionContext) -> Any:
    """One value of a condition, read when it is dynamic and left alone when it is not.

        item_of(Today(), "made_at", resolution)     ->  date(2026, 10, 24)
        item_of("SAL", "journal", resolution)       ->  "SAL"
        item_of(Period(name="this_month"), "made_at", resolution)
            ->  InvalidCriteria: a period is a range and cannot be one item of a list; …

    Context: a period is a range, so it stands only for a whole `between`, never one item.
    """
    if isinstance(value, Period):
        raise exceptions.InvalidCriteria(
            f"a period is a range and cannot be one item of a list; '{field}' asked for it so"
        )
    if isinstance(value, (Today, Now, FromContext, Param)):
        return value_of(value, resolution)
    return value
