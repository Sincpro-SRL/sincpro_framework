"""Values a filter can hold besides a literal: a day that is not fixed, a user, a call's parameter.

        {"field": "due", "operator": "<", "value": {"$": "today"}}

A dynamic value is stored as itself and read when the filter is used, so a saved view that says
`today` means the day it is opened. It is an object with the reserved key `"$"` naming what it
is; no literal can collide with it. `services/dynamic_values.py` turns it into a literal, on the
server, with the server's clock and context.

Dates move with `plus` (a signed `Span`) and then snap to the start of a calendar unit with
`start_of`: thirty days ago is `{"$": "today", "plus": {"days": -30}}` and the first of this month
is `{"$": "today", "start_of": "month"}`. Both are allowed on any value that is a date: `today`,
`now`, and what `context` or `param` read.

In Python the nodes are written as their classes and shifted with `before`, `after` and
`at_start_of`; none of them is on the wire, which only carries the signed `plus`.
"""

import calendar
from datetime import UTC, date, datetime, timedelta, tzinfo
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import ConfigDict, Field, model_validator

from sincpro_framework.ddd import exceptions
from sincpro_framework.ddd.criteria.domain.resolution import ResolutionContext
from sincpro_framework.ddd.criteria.domain.strict import Strict


class Grain(StrEnum):
    """A calendar unit a date snaps to the start of."""

    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"

    @property
    def span(self) -> "Span":
        """One unit of this grain, as the span that moves a date by it.

        >>> Grain.QUARTER.span
        Span(months=3)
        """
        match self:
            case Grain.DAY:
                return Span(days=1)
            case Grain.WEEK:
                return Span(weeks=1)
            case Grain.MONTH:
                return Span(months=1)
            case Grain.QUARTER:
                return Span(months=3)
            case Grain.YEAR:
                return Span(years=1)

    def snap[D: date](self, day: D, week_starts_on: int = 0) -> D:
        """A day snapped to the start of the unit of this grain it falls in; a datetime also
        loses its time.

            Grain.QUARTER.snap(date(2026, 8, 14))          ->  date(2026, 7, 1)
            Grain.WEEK.snap(date(2026, 10, 24))            ->  date(2026, 10, 19)  # Monday
            Grain.WEEK.snap(date(2026, 10, 24), 6)         ->  date(2026, 10, 18)  # Sunday
            Grain.MONTH.snap(datetime(2026, 10, 24, 15, 9))
                ->  datetime(2026, 10, 1, 0, 0)

        Context: `week_starts_on` counts as `date.weekday()` does — 0 is Monday; only a week
        reads it.
        """
        match self:
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

    def label(self, day: date) -> str:
        """The text a database writes for the unit of this grain a date falls in.

            Grain.WEEK.label(date(2026, 3, 14))        ->  '2026-W11'    # ISO year and week
            Grain.QUARTER.label(date(2026, 8, 2))      ->  '2026-Q3'
            Grain.MONTH.label(date(2026, 10, 24))      ->  '2026-10'
            Grain.YEAR.label(date(2026, 10, 24))       ->  '2026'
            Grain.DAY.label(date(2026, 10, 24))        ->  '2026-10-24'

        Context: the date is labelled as it is — snapping it to its grain is the caller's.
        """
        match self:
            case Grain.YEAR:
                return f"{day.year}"
            case Grain.QUARTER:
                return f"{day.year}-Q{(day.month - 1) // 3 + 1}"
            case Grain.MONTH:
                return f"{day.year}-{day.month:02d}"
            case Grain.WEEK:
                year, week, _ = day.isocalendar()
                return f"{year}-W{week:02d}"
            case Grain.DAY:
                return day.isoformat()


class Part(StrEnum):
    """A number extracted from a date: the month of the year, whatever the year.

    A grain **cuts** (a bucket is a range of time) and a part **extracts** (a bucket is a number
    that many dates share), so they answer different questions: sales by month `2026-03` is a
    grain, sales by «March, in any year» is a part. Weekdays count from Monday as 1, as ISO does.
    """

    YEAR = "year"
    QUARTER = "quarter"
    MONTH = "month"
    ISO_WEEK = "iso_week"
    DAY_OF_WEEK = "day_of_week"
    DAY_OF_MONTH = "day_of_month"
    HOUR = "hour"

    def of(self, value: Any) -> int | None:
        """The number this part is of a date: what a database extracts from the column.

            Part.MONTH.of(datetime(2026, 3, 14, 15, 9))   ->  3
            Part.DAY_OF_WEEK.of(date(2026, 3, 14))        ->  6     # Saturday; Monday 1
            Part.QUARTER.of("2026-08-14")                 ->  3     # text read as ISO
            Part.HOUR.of(date(2026, 3, 14))               ->  0     # a date has no hour
            Part.MONTH.of(None)                           ->  None

        1. No value: `None` — a row with no date has no part.
        2. Text is read as an ISO date or datetime.
        3. The part:
           3.1 `YEAR`, `MONTH`, `DAY_OF_MONTH`, `HOUR` — the field of the value, a date's hour 0;
           3.2 `QUARTER` — the month's quarter, 1 to 4;
           3.3 `ISO_WEEK` — the ISO week number;
           3.4 `DAY_OF_WEEK` — the ISO weekday, Monday 1 to Sunday 7.
        Final: that number, as a database's `EXTRACT` would answer it.
        """
        if value is None:
            return None
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        match self:
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


PART_VALUES: dict[Part, tuple[int, int] | None] = {
    Part.YEAR: None,
    Part.QUARTER: (1, 4),
    Part.MONTH: (1, 12),
    Part.ISO_WEEK: (1, 53),
    Part.DAY_OF_WEEK: (1, 7),
    Part.DAY_OF_MONTH: (1, 31),
    Part.HOUR: (0, 23),
}
"""The numbers each part can take, for the groups that `fill` lists; a year has no fixed range."""


class ValueKind(StrEnum):
    """What a dynamic value is. The set is closed: the name decides what runs."""

    TODAY = "today"
    NOW = "now"
    PERIOD = "period"
    CONTEXT = "context"
    PARAM = "param"
    FIELD = "field"


class Periods(StrEnum):
    """A calendar span, as a closed range `[start, end)`. Only with the operator `between`."""

    TODAY = "today"
    YESTERDAY = "yesterday"
    TOMORROW = "tomorrow"
    THIS_WEEK = "this_week"
    LAST_WEEK = "last_week"
    NEXT_WEEK = "next_week"
    THIS_MONTH = "this_month"
    LAST_MONTH = "last_month"
    NEXT_MONTH = "next_month"
    THIS_QUARTER = "this_quarter"
    LAST_QUARTER = "last_quarter"
    NEXT_QUARTER = "next_quarter"
    THIS_YEAR = "this_year"
    LAST_YEAR = "last_year"
    NEXT_YEAR = "next_year"


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


def _is_none(value: Any) -> bool:
    """`exclude_if` for an optional part: a unit nobody asked for does not travel.

    Span(days=-30).model_dump()  ->  {"days": -30}       not six `None`s beside it
    """
    return value is None


class Span(Strict):
    """A signed amount of time, by unit. Only the units asked for travel.

    {"days": -30}                       thirty days back
    {"months": 1, "days": -1}           a month ahead, a day back
    """

    model_config = ConfigDict(frozen=True)

    minutes: int | None = Field(default=None, exclude_if=_is_none)
    hours: int | None = Field(default=None, exclude_if=_is_none)
    days: int | None = Field(default=None, exclude_if=_is_none)
    weeks: int | None = Field(default=None, exclude_if=_is_none)
    months: int | None = Field(default=None, exclude_if=_is_none)
    years: int | None = Field(default=None, exclude_if=_is_none)

    def shift_on_calendar[D: date](self, day: D) -> D:
        """A day moved by the calendar units of this span: months and years first — a month
        end clamped, 31 January plus one month is 28 February — then days and weeks."""
        month_index = (
            day.year * 12 + day.month - 1 + (self.months or 0) + 12 * (self.years or 0)
        )
        year, month = divmod(month_index, 12)
        last = calendar.monthrange(year, month + 1)[1]
        moved = day.replace(year=year, month=month + 1, day=min(day.day, last))
        return moved + timedelta(days=(self.days or 0) + 7 * (self.weeks or 0))

    def shift(self, day: date | datetime, timezone: tzinfo) -> date | datetime:
        """A day moved by this span. Calendar units follow the wall clock of the timezone, so a
        day across a clock change is still a day; hours and minutes are elapsed time.

            in      2026-01-31, months=1          →  out  2026-02-28
            in      2026-10-12, days=-30          →  out  2026-09-12
        """
        if not isinstance(day, datetime):
            if self.hours or self.minutes:
                raise exceptions.InvalidCriteria(
                    "a date has no hours or minutes; use `now` to move by them"
                )
            return self.shift_on_calendar(day)

        wall = self.shift_on_calendar(day.astimezone(timezone).replace(tzinfo=None))
        elapsed = timedelta(hours=self.hours or 0, minutes=self.minutes or 0)
        return wall.replace(tzinfo=timezone).astimezone(UTC) + elapsed


class Shiftable(Strict):
    """A value that is a date, and so can be moved and snapped. `plus` is applied first."""

    model_config = ConfigDict(frozen=True, populate_by_name=True, serialize_by_alias=True)

    plus: Span | None = Field(default=None, exclude_if=_is_none)
    """How far to move it, signed: `{"days": -30}` is thirty days ago."""

    start_of: Grain | None = Field(default=None, exclude_if=_is_none)
    """The start of the calendar unit it falls in once moved: `month` is the first of the month."""

    def after(
        self,
        minutes: int = 0,
        hours: int = 0,
        days: int = 0,
        weeks: int = 0,
        months: int = 0,
        years: int = 0,
    ) -> Self:
        """The same value moved forward, on top of what it already moved.

        >>> Today().after(days=7)
        Today(plus=Span(days=7))
        """
        moved = {
            unit: (getattr(self.plus, unit, None) or 0) + amount
            for unit, amount in zip(
                Span.model_fields,
                (minutes, hours, days, weeks, months, years),
            )
        }
        return self.model_copy(
            update={
                "plus": Span(**{unit: amount for unit, amount in moved.items() if amount})
            }
        )

    def before(
        self,
        minutes: int = 0,
        hours: int = 0,
        days: int = 0,
        weeks: int = 0,
        months: int = 0,
        years: int = 0,
    ) -> Self:
        """The same value moved back: `Today().before(days=30)` is thirty days ago."""
        return self.after(-minutes, -hours, -days, -weeks, -months, -years)

    def at_start_of(self, grain: Grain) -> Self:
        """The same value snapped to the start of a calendar unit, after any move.

        >>> Today().at_start_of(Grain.MONTH)
        Today(start_of=<Grain.MONTH: 'month'>)
        """
        return self.model_copy(update={"start_of": grain})


class Today(Shiftable):
    """The current date, in the context's timezone."""

    resolver: Literal[ValueKind.TODAY] = Field(default=ValueKind.TODAY, alias="$")


class Now(Shiftable):
    """The current instant, in UTC."""

    resolver: Literal[ValueKind.NOW] = Field(default=ValueKind.NOW, alias="$")


class FromContext(Shiftable):
    """A value of the server's own context — the user, the tenant, a date it holds — never one the
    client sends."""

    resolver: Literal[ValueKind.CONTEXT] = Field(default=ValueKind.CONTEXT, alias="$")

    key: str
    """The key it reads, as the context holds it."""


class Param(Shiftable):
    """A value the caller supplied for this call: a typed text, a step's result."""

    resolver: Literal[ValueKind.PARAM] = Field(default=ValueKind.PARAM, alias="$")

    name: str
    """The parameter, or a path into it with dots: `steps.order.total`."""


class FromField(Strict):
    """Another field of the same row: a column compared with a column.

        {"field": "total", "operator": ">", "value": {"$": "field", "name": "credit_limit"}}

    It is not read before the row: it is the other side of the comparison, and it takes only the
    operators that compare (`=`, `!=`, `<`, `<=`, `>`, `>=`) and a field of the same type.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True, serialize_by_alias=True)

    resolver: Literal[ValueKind.FIELD] = Field(default=ValueKind.FIELD, alias="$")

    name: str
    """The field it reads, of the same entity."""


class Period(Strict):
    """A span of the calendar as a range `[start, end)`: only with the operator `between`.

    Either a named one, or a window of whole units counted from the current one:

        {"$": "period", "name": "this_month"}
        {"$": "period", "unit": "day", "last": 30}                      the 30 days before today
        {"$": "period", "unit": "week", "next": 2, "include_current": true}

    `last` and `next` leave the current unit out unless `include_current` is set.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True, serialize_by_alias=True)

    resolver: Literal[ValueKind.PERIOD] = Field(default=ValueKind.PERIOD, alias="$")

    name: Periods | None = Field(default=None, exclude_if=_is_none)
    unit: Grain | None = Field(default=None, exclude_if=_is_none)
    last: int | None = Field(default=None, exclude_if=_is_none)
    next: int | None = Field(default=None, exclude_if=_is_none)
    include_current: bool = Field(default=False, exclude_if=lambda one: one is False)

    @model_validator(mode="after")
    def _a_name_or_a_window(self) -> "Period":
        """A period is one named span, or `unit` with how many before (`last`) or after (`next`).

        >>> Period(unit=Grain.DAY, last=30)
        Period(name=None, unit=<Grain.DAY: 'day'>, last=30, next=None, include_current=False)
        >>> Period(unit=Grain.DAY)
        InvalidCriteria: a period is a name, or a unit with `last` or `next`
        """
        counted = [one for one in (self.last, self.next) if one is not None]
        if self.name is not None:
            if self.unit is not None or counted or self.include_current:
                raise exceptions.InvalidCriteria(
                    "a named period takes nothing else; `unit`, `last`, `next` and "
                    "`include_current` describe a window"
                )
            return self
        if self.unit is None or len(counted) != 1:
            raise exceptions.InvalidCriteria(
                "a period is a name, or a unit with `last` or `next`: "
                '{"$": "period", "unit": "day", "last": 30}'
            )
        if counted[0] < 1:
            raise exceptions.InvalidCriteria(
                f"a period counts at least one unit; got {counted[0]}"
            )
        return self

    def _window(
        self, today: date, resolution: ResolutionContext
    ) -> tuple[date | datetime, date | datetime]:
        """A window of whole units counted from the current one: the `last` N before it, or the
        `next` N after it, with or without the current unit itself."""
        assert self.unit is not None
        unit = self.unit.span
        current = self.unit.snap(today, resolution.week_starts_on)
        count = self.last if self.last is not None else self.next
        assert count is not None

        def units(amount: int) -> date | datetime:
            """The start of the unit `amount` units away from the current one.

            unit = month, current = 2026-03-01:   units(-2)  ->  2026-01-01
            """
            scaled = Span(
                **{
                    name: amount * step
                    for name, step in unit.model_dump(exclude_none=True).items()
                }
            )
            return scaled.shift(current, resolution.timezone)

        if self.last is not None:
            return units(-(count - 1 if self.include_current else count)), units(
                1 if self.include_current else 0
            )
        return units(0 if self.include_current else 1), units(
            count + (0 if self.include_current else 1)
        )

    def range(self, resolution: ResolutionContext) -> tuple[date | datetime, date | datetime]:
        """The `[start, end)` this period covers, in dates of the context's timezone.

            # now = 2026-10-24 15:00 UTC, weeks starting on Monday
            Period(name="last_month").range(resolution)
                ->  (date(2026, 9, 1), date(2026, 10, 1))
            Period(unit="day", last=30).range(resolution)
                ->  (date(2026, 9, 24), date(2026, 10, 24))     # today left out
            Period(unit="week", next=2, include_current=True).range(resolution)
                ->  (date(2026, 10, 19), date(2026, 11, 2))

        Context: a named period is its unit moved from today and snapped to its start, one unit
        long; a window is counted from the current unit by `_window`.
        """
        today = resolution.now.astimezone(resolution.timezone).date()
        if self.name is None:
            return self._window(today, resolution)
        grain, away = PERIOD_STARTS[self.name]
        scaled = Span(
            **{
                name: amount * away
                for name, amount in grain.span.model_dump(exclude_none=True).items()
            }
        )
        moved = scaled.shift(today, resolution.timezone)
        start = grain.snap(moved, resolution.week_starts_on)
        return start, grain.span.shift(start, resolution.timezone)


Dynamic = Annotated[
    Today | Now | FromContext | Param | Period | FromField, Field(discriminator="resolver")
]

DYNAMIC = (Today, Now, FromContext, Param, Period, FromField)
"""The classes of a dynamic value, for asking `isinstance`."""

RESOLVABLE = (Today, Now, FromContext, Param, Period)
"""The dynamic values read before any row is: all but `FromField`, which is the row's own."""

TEXT = Param(name="text")
"""Where a template leaves the text a person typed: an entity's `DEFAULT_LITERAL_SEARCH`.

    Condition(field="code", operator=Operator.STARTS_WITH, value=TEXT)

`Node.with_text` puts the literal in its place. It is a value, so a template is still a `Criteria`
that travels as JSON — `{"$": "param", "name": "text"}` — and a client fills the same way."""


def item_kind(value: Any) -> str:
    """Which side of a value's union an item belongs to: a dynamic value or a literal.

        item_kind({"$": "today"})   ->  "dynamic"
        item_kind(Today())          ->  "dynamic"
        item_kind({"a": 1})         ->  "literal"    # a dict without `$` is a literal
        item_kind("2026-01-01")     ->  "literal"
        item_kind({"$": "todya"})
            ->  InvalidCriteria: 'todya' is not a value the engine resolves; use one of …

    Context: a `$` naming no `ValueKind` is refused rather than read as a literal — a typo
    would otherwise filter on the dict itself.
    """
    if isinstance(value, DYNAMIC):
        return "dynamic"
    if isinstance(value, dict) and "$" in value:
        if value["$"] not in {one.value for one in ValueKind}:
            raise exceptions.InvalidCriteria(
                f"'{value['$']}' is not a value the engine resolves; "
                f"use one of {', '.join(one.value for one in ValueKind)}"
            )
        return "dynamic"
    return "literal"


def value_kind(value: Any) -> str:
    """What a condition's value is: a list of items, or one item. A list is told apart first,
    because its items may be dynamic: `code in [{"$": "param", …}, "0"]`."""
    return "list" if isinstance(value, list) else "item"


class ResolverMeta(Strict):
    """What the server offers as a dynamic value, so a filter builder lists them without being told
    twice: a keyword a project registers appears in every client with no client release."""

    name: str
    returns: str
    """What it reads to: `date`, `datetime`, `range`, `field`, or `any` for what a key holds."""

    params: dict[str, str]
    """Each parameter and its kind: `span`, `grain`, `period`, `integer`, `boolean`, `text`."""

    choices: dict[str, list[str]] = {}
    """The values a parameter may take, when it takes only some."""


_UNITS = [one for one in Span.model_fields]
_GRAINS = [one.value for one in Grain]
_SHIFT = {"plus": "span", "start_of": "grain"}
_SHIFT_CHOICES = {"plus": _UNITS, "start_of": _GRAINS}

RESOLVERS: tuple[ResolverMeta, ...] = (
    ResolverMeta(name="today", returns="date", params=_SHIFT, choices=_SHIFT_CHOICES),
    ResolverMeta(name="now", returns="datetime", params=_SHIFT, choices=_SHIFT_CHOICES),
    ResolverMeta(
        name="period",
        returns="range",
        params={
            "name": "period",
            "unit": "grain",
            "last": "integer",
            "next": "integer",
            "include_current": "boolean",
        },
        choices={"name": [one.value for one in Periods], "unit": _GRAINS},
    ),
    ResolverMeta(
        name="context",
        returns="any",
        params={"key": "text", **_SHIFT},
        choices=_SHIFT_CHOICES,
    ),
    ResolverMeta(
        name="param", returns="any", params={"name": "text", **_SHIFT}, choices=_SHIFT_CHOICES
    ),
    ResolverMeta(name="field", returns="field", params={"name": "text"}),
)
"""Every dynamic value the engine reads, as `Meta.resolvers` publishes it."""
