"""Values a filter can hold besides a literal: a day that is not fixed, a user, a call's parameter.

        {"field": "due", "operator": "<", "value": {"$": "today"}}

A dynamic value is stored as itself and read when the filter is used, so a saved view that says
`today` means the day it is opened. It is an object with the reserved key `"$"` naming what it
is; no literal can collide with it. `resolve.py` turns it into a literal, on the server, with the
server's clock and context.

Dates move with `plus` (a signed `Span`) and then snap to the start of a calendar unit with
`start_of`: thirty days ago is `{"$": "today", "plus": {"days": -30}}` and the first of this month
is `{"$": "today", "start_of": "month"}`. Both are allowed on any value that is a date: `today`,
`now`, and what `context` or `param` read.

In Python the nodes are written as their classes and shifted with `before`, `after` and
`at_start_of`; none of them is on the wire, which only carries the signed `plus`.
"""

from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import ConfigDict, Field, model_validator

from sincpro_framework.ddd.criteria.domain.strict import Strict
from sincpro_framework.ddd.exceptions import InvalidCriteria


class Grain(StrEnum):
    """A calendar unit a date snaps to the start of."""

    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


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


def _is_none(value: Any) -> bool:
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
                raise InvalidCriteria(
                    "a named period takes nothing else; `unit`, `last`, `next` and "
                    "`include_current` describe a window"
                )
            return self
        if self.unit is None or len(counted) != 1:
            raise InvalidCriteria(
                "a period is a name, or a unit with `last` or `next`: "
                '{"$": "period", "unit": "day", "last": 30}'
            )
        if counted[0] < 1:
            raise InvalidCriteria(f"a period counts at least one unit; got {counted[0]}")
        return self


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

`with_text` puts the literal in its place. It is a value, so a template is still a `Criteria`
that travels as JSON — `{"$": "param", "name": "text"}` — and a client fills the same way."""


def item_kind(value: Any) -> str:
    """Which side of a value's union an item belongs to: a dynamic value or a literal.

    in      {"$": "today"}, Today()      →  out  "dynamic"
    in      {"a": 1}, "2026-01-01", 3    →  out  "literal"
    in      {"$": "todya"}               →  InvalidCriteria naming what is known
    """
    if isinstance(value, DYNAMIC):
        return "dynamic"
    if isinstance(value, dict) and "$" in value:
        if value["$"] not in {one.value for one in ValueKind}:
            raise InvalidCriteria(
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
