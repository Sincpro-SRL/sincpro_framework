"""Calendar arithmetic over dates and instants: move a day, snap it to the start of a unit, and
find the range a named period covers. Pure: what is asked is in the arguments, the clock is not.
"""

import calendar
from datetime import UTC, date, datetime, timedelta

from sincpro_framework.ddd.criteria.domain.resolution import ResolutionContext
from sincpro_framework.ddd.criteria.domain.values import Grain, Period, Periods, Span
from sincpro_framework.ddd.exceptions import InvalidCriteria

PERIOD_STARTS: dict[Periods, tuple[Grain, int]] = {
    Periods.TODAY: (Grain.DAY, 0),
    Periods.YESTERDAY: (Grain.DAY, -1),
    Periods.TOMORROW: (Grain.DAY, 1),
    Periods.THIS_WEEK: (Grain.WEEK, 0),
    Periods.LAST_WEEK: (Grain.WEEK, -1),
    Periods.NEXT_WEEK: (Grain.WEEK, 1),
    Periods.THIS_MONTH: (Grain.MONTH, 0),
    Periods.LAST_MONTH: (Grain.MONTH, -1),
    Periods.NEXT_MONTH: (Grain.MONTH, 1),
    Periods.THIS_QUARTER: (Grain.QUARTER, 0),
    Periods.LAST_QUARTER: (Grain.QUARTER, -1),
    Periods.NEXT_QUARTER: (Grain.QUARTER, 1),
    Periods.THIS_YEAR: (Grain.YEAR, 0),
    Periods.LAST_YEAR: (Grain.YEAR, -1),
    Periods.NEXT_YEAR: (Grain.YEAR, 1),
}
"""Each named period: the calendar unit it spans, and how many of them from the current one."""


ONE_OF: dict[Grain, Span] = {
    Grain.DAY: Span(days=1),
    Grain.WEEK: Span(weeks=1),
    Grain.MONTH: Span(months=1),
    Grain.QUARTER: Span(months=3),
    Grain.YEAR: Span(years=1),
}


def _with_months[D: date](day: D, months: int) -> D:
    """The same day some months on, a month end clamped: 31 January plus one month is 28 February."""
    month_index = day.year * 12 + day.month - 1 + months
    year, month = divmod(month_index, 12)
    last = calendar.monthrange(year, month + 1)[1]
    return day.replace(year=year, month=month + 1, day=min(day.day, last))


def snapped[D: date](day: D, grain: Grain, week_starts_on: int) -> D:
    """The start of the calendar unit a day falls in; a datetime also loses its time."""
    match grain:
        case Grain.DAY:
            start = day
        case Grain.WEEK:
            start = day - timedelta(days=(day.weekday() - week_starts_on) % 7)
        case Grain.MONTH:
            start = day.replace(day=1)
        case Grain.QUARTER:
            start = day.replace(month=3 * ((day.month - 1) // 3) + 1, day=1)
        case Grain.YEAR:
            start = day.replace(month=1, day=1)
    if isinstance(start, datetime):
        return start.replace(hour=0, minute=0, second=0, microsecond=0)
    return start


def on_calendar[D: date](day: D, span: Span) -> D:
    """A day moved by the calendar units of a span: months and years first, then days and weeks."""
    moved = _with_months(day, (span.months or 0) + 12 * (span.years or 0))
    return moved + timedelta(days=(span.days or 0) + 7 * (span.weeks or 0))


def moved(day: date | datetime, span: Span, resolution: ResolutionContext) -> date | datetime:
    """A day moved by a span. Calendar units follow the wall clock of the timezone, so a day
    across a clock change is still a day; hours and minutes are elapsed time.

        in      2026-01-31, months=1          →  out  2026-02-28
        in      2026-10-12, days=-30          →  out  2026-09-12
    """
    if not isinstance(day, datetime):
        if span.hours or span.minutes:
            raise InvalidCriteria("a date has no hours or minutes; use `now` to move by them")
        return on_calendar(day, span)

    wall = on_calendar(day.astimezone(resolution.timezone).replace(tzinfo=None), span)
    elapsed = timedelta(hours=span.hours or 0, minutes=span.minutes or 0)
    return wall.replace(tzinfo=resolution.timezone).astimezone(UTC) + elapsed


def _window(
    period: Period, today: date, resolution: ResolutionContext
) -> tuple[date | datetime, date | datetime]:
    """A window of whole units counted from the current one: the `last` N before it, or the
    `next` N after it, with or without the current unit itself."""
    assert period.unit is not None
    unit = ONE_OF[period.unit]
    current = snapped(today, period.unit, resolution.week_starts_on)
    count = period.last if period.last is not None else period.next
    assert count is not None

    def units(amount: int) -> date | datetime:
        return moved(
            current,
            Span(
                **{
                    name: amount * step
                    for name, step in unit.model_dump(exclude_none=True).items()
                }
            ),
            resolution,
        )

    if period.last is not None:
        return units(-(count - 1 if period.include_current else count)), units(
            1 if period.include_current else 0
        )
    return units(0 if period.include_current else 1), units(
        count + (0 if period.include_current else 1)
    )


def range_of(
    period: Period, resolution: ResolutionContext
) -> tuple[date | datetime, date | datetime]:
    """The `[start, end)` a period covers, in dates of the context's timezone."""
    today = resolution.now.astimezone(resolution.timezone).date()
    if period.name is None:
        return _window(period, today, resolution)
    grain, away = PERIOD_STARTS[period.name]
    unit = ONE_OF[grain]
    scaled = Span(
        **{name: amount * away for name, amount in unit.model_dump(exclude_none=True).items()}
    )
    start = snapped(moved(today, scaled, resolution), grain, resolution.week_starts_on)
    return start, moved(start, unit, resolution)
