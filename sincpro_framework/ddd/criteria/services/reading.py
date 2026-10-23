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
from sincpro_framework.ddd.criteria.services.calendar import moved, snapped


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
    """What a value reads, moved by its `plus` and then snapped to its `start_of`."""
    if value.plus is None and value.start_of is None:
        return anchor

    day = _date_read(anchor, "a value that is moved or snapped")
    if isinstance(day, datetime) and day.tzinfo is None:
        day = day.replace(tzinfo=UTC)

    if value.plus is not None:
        day = moved(day, value.plus, resolution)
    if value.start_of is not None:
        if isinstance(day, datetime):
            local = day.astimezone(resolution.timezone).replace(tzinfo=None)
            start = snapped(local, value.start_of, resolution.week_starts_on)
            return start.replace(tzinfo=resolution.timezone).astimezone(UTC)
        return snapped(day, value.start_of, resolution.week_starts_on)
    return day


def value_of(value: Today | Now | FromContext | Param, resolution: ResolutionContext) -> Any:
    """The literal a dynamic value stands for."""
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
    """One value of a condition, read when it is dynamic and left alone when it is not."""
    if isinstance(value, Period):
        raise exceptions.InvalidCriteria(
            f"a period is a range and cannot be one item of a list; '{field}' asked for it so"
        )
    if isinstance(value, (Today, Now, FromContext, Param)):
        return value_of(value, resolution)
    return value
