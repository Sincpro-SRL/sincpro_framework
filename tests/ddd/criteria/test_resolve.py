"""A value that is read when the filter is used: `today`, the user, a call's parameter.

The tree is stored symbolic and resolved on the server with one instant, so what these tests pin
is the clock: every case says what day it is, and what the filter means on that day.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from sincpro_framework.context.entrypoint.facade import carrying
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import AggregateRepository, Criteria, FieldType
from sincpro_framework.ddd.criteria import (
    All,
    Condition,
    FromContext,
    FromField,
    Grain,
    Grouping,
    Level,
    Measure,
    Now,
    Operator,
    Param,
    Period,
    Periods,
    ResolutionContext,
    Span,
    Today,
    conditions_of,
    has_dynamic_values,
    resolve,
)
from sincpro_framework.ddd.entity import Entity
from sincpro_framework.ddd.entity.entity_meta import describe_class
from sincpro_framework.ddd.exceptions import InvalidCriteria
from sincpro_framework.exceptions import ProgrammingError

LA_PAZ = ZoneInfo("America/La_Paz")
MONDAY = datetime(2026, 10, 12, 15, 30, tzinfo=UTC)


def on(now: datetime, **more) -> ResolutionContext:
    return ResolutionContext(now=now, **more)


def due(value, operator=Operator.EQ) -> Condition:
    return Condition(field="due", operator=operator, value=value)


def read(value, now: datetime = MONDAY, operator=Operator.EQ, **more) -> Any:
    resolved = resolve(due(value, operator), on(now, **more))
    assert isinstance(resolved, Condition)
    return resolved.value


# ── what travels ───────────────────────────────────────────────────────────────


def test_a_dynamic_value_is_an_object_with_the_reserved_key():
    written = due(Today().before(days=30).at_start_of(Grain.MONTH)).model_dump(
        mode="json", exclude_none=True
    )

    assert written["value"] == {"$": "today", "plus": {"days": -30}, "start_of": "month"}


def test_it_comes_back_from_json_as_the_same_node():
    node = Today().before(days=30)
    written = due(node).model_dump(mode="json", exclude_none=True)

    assert Condition.model_validate(written).value == node


def test_an_object_without_the_key_is_still_a_literal():
    assert Condition(field="meta", value={"today": 1}).value == {"today": 1}


def test_an_unknown_name_is_refused_naming_what_is_known():
    with pytest.raises(InvalidCriteria, match="'todya' is not a value the engine resolves"):
        Condition.model_validate({"field": "due", "value": {"$": "todya"}})


def test_moving_a_value_adds_to_what_it_already_moved():
    assert Today().before(days=30).after(days=10).plus == Span(days=-20)


# ── a day ──────────────────────────────────────────────────────────────────────


def test_today_is_the_date_of_the_instant():
    assert read(Today()) == date(2026, 10, 12)


def test_thirty_days_ago():
    assert read(Today().before(days=30)) == date(2026, 9, 12)


def test_a_month_end_is_clamped_and_not_overflowed():
    january_31 = datetime(2026, 1, 31, 9, tzinfo=UTC)

    assert read(Today().after(months=1), january_31) == date(2026, 2, 28)
    assert read(Today().after(months=1), datetime(2028, 1, 31, tzinfo=UTC)) == date(
        2028, 2, 29
    )


def test_years_and_months_move_before_days():
    assert read(Today().after(years=1, months=-1, days=1)) == date(2027, 9, 13)


@pytest.mark.parametrize(
    ("grain", "expected"),
    [
        (Grain.DAY, date(2026, 10, 12)),
        (Grain.WEEK, date(2026, 10, 12)),
        (Grain.MONTH, date(2026, 10, 1)),
        (Grain.QUARTER, date(2026, 10, 1)),
        (Grain.YEAR, date(2026, 1, 1)),
    ],
)
def test_the_start_of_a_calendar_unit(grain, expected):
    assert read(Today().at_start_of(grain)) == expected


def test_a_week_starts_on_the_weekday_the_context_says():
    wednesday = datetime(2026, 10, 14, tzinfo=UTC)

    assert read(Today().at_start_of(Grain.WEEK), wednesday) == date(2026, 10, 12)
    assert read(Today().at_start_of(Grain.WEEK), wednesday, week_starts_on=6) == date(
        2026, 10, 11
    )


def test_a_move_comes_before_the_snap():
    """Odoo's `today -1m =1d`: the first day of last month, not a month before the first."""
    assert read(Today().before(months=1).at_start_of(Grain.MONTH)) == date(2026, 9, 1)
    assert read(Today().after(days=30).at_start_of(Grain.MONTH)) == date(2026, 11, 1)


def test_a_date_has_no_hours():
    with pytest.raises(InvalidCriteria, match="a date has no hours"):
        read(Today().after(hours=2))


# ── an instant, and the timezone ───────────────────────────────────────────────


def test_now_is_the_instant_in_utc():
    assert read(Now()) == MONDAY
    assert read(Now()).tzinfo == UTC


def test_hours_are_elapsed_time():
    assert read(Now().before(hours=2)) == datetime(2026, 10, 12, 13, 30, tzinfo=UTC)


def test_a_timezone_decides_which_day_it_is():
    """02:00 UTC on the 13th is still the evening of the 12th in La Paz (UTC-4)."""
    late = datetime(2026, 10, 13, 2, 0, tzinfo=UTC)

    assert read(Today(), late) == date(2026, 10, 13)
    assert read(Today(), late, timezone=LA_PAZ) == date(2026, 10, 12)


def test_the_start_of_a_day_in_a_timezone_is_a_utc_instant():
    late = datetime(2026, 10, 13, 2, 0, tzinfo=UTC)

    start = read(Now().at_start_of(Grain.DAY), late, timezone=LA_PAZ)

    assert start == datetime(2026, 10, 12, 4, 0, tzinfo=UTC)


def test_a_day_across_a_clock_change_is_still_a_day():
    """Santiago moves its clocks on 2026-09-06: a day later is the same wall time, 23 hours on."""
    santiago = ZoneInfo("America/Santiago")
    before_change = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)

    moved = read(Now().after(days=1), before_change, timezone=santiago)

    assert moved - before_change == timedelta(hours=23)


def test_one_instant_serves_every_value_of_a_filter():
    now = datetime(2026, 10, 12, 23, 59, 59, tzinfo=UTC)
    both = All(all=[due(Today()), Condition(field="at", value=Now())])

    resolved = resolve(both, on(now))

    assert [one.value for one in conditions_of(resolved)] == [date(2026, 10, 12), now]


# ── the context and the parameters ─────────────────────────────────────────────


def test_a_value_of_the_servers_context():
    resolved = read(FromContext(key="user_id"), context={"user_id": "u-7"})

    assert resolved == "u-7"


def test_a_date_the_context_holds_can_be_moved():
    report = date(2026, 6, 15)

    assert read(
        FromContext(key="report_date").before(months=3).at_start_of(Grain.MONTH),
        context={"report_date": report},
    ) == date(2026, 3, 1)


def test_a_date_the_context_holds_as_text_is_read():
    assert read(
        FromContext(key="report_date").before(days=1), context={"report_date": "2026-06-15"}
    ) == date(2026, 6, 14)


def test_a_key_the_context_does_not_hold_is_refused():
    with pytest.raises(InvalidCriteria, match="the context has no 'user_id'"):
        read(FromContext(key="user_id"))


def test_a_parameter_is_found_by_a_path():
    resolved = read(
        Param(name="steps.order.total"), params={"steps": {"order": {"total": 9}}}
    )

    assert resolved == 9


def test_a_value_that_is_not_a_date_cannot_be_moved():
    with pytest.raises(InvalidCriteria, match="is not a date"):
        read(FromContext(key="user_id").before(days=1), context={"user_id": "u-7"})


# ── a period is a range ────────────────────────────────────────────────────────


def between(name: Periods, now: datetime = MONDAY, **more):
    return resolve(due(Period(name=name), Operator.BETWEEN), on(now, **more))


def test_this_month_is_closed_at_the_start_and_open_at_the_end():
    resolved = between(Periods.THIS_MONTH)

    assert resolved == All(
        all=[
            Condition(field="due", operator=Operator.GTE, value=date(2026, 10, 1)),
            Condition(field="due", operator=Operator.LT, value=date(2026, 11, 1)),
        ]
    )


@pytest.mark.parametrize(
    ("name", "start", "end"),
    [
        (Periods.TODAY, date(2026, 10, 12), date(2026, 10, 13)),
        (Periods.YESTERDAY, date(2026, 10, 11), date(2026, 10, 12)),
        (Periods.THIS_WEEK, date(2026, 10, 12), date(2026, 10, 19)),
        (Periods.LAST_WEEK, date(2026, 10, 5), date(2026, 10, 12)),
        (Periods.LAST_MONTH, date(2026, 9, 1), date(2026, 10, 1)),
        (Periods.THIS_QUARTER, date(2026, 10, 1), date(2027, 1, 1)),
        (Periods.LAST_QUARTER, date(2026, 7, 1), date(2026, 10, 1)),
        (Periods.NEXT_YEAR, date(2027, 1, 1), date(2028, 1, 1)),
    ],
)
def test_the_named_periods(name, start, end):
    assert [one.value for one in conditions_of(between(name))] == [start, end]


def test_last_month_in_january_is_december_of_the_year_before():
    resolved = between(Periods.LAST_MONTH, datetime(2026, 1, 20, tzinfo=UTC))

    assert [one.value for one in conditions_of(resolved)] == [
        date(2025, 12, 1),
        date(2026, 1, 1),
    ]


def test_a_period_asked_with_equals_is_refused():
    with pytest.raises(InvalidCriteria, match="takes the operator 'between'"):
        resolve(due(Period(name=Periods.THIS_MONTH)), on(MONDAY))


# ── what is not dynamic is left alone ──────────────────────────────────────────


def test_a_tree_with_nothing_dynamic_comes_back_as_it_came():
    tree = All(all=[due("2026-01-01"), due("x")])

    assert resolve(tree, on(MONDAY)) is tree
    assert resolve(None, on(MONDAY)) is None


def test_a_group_keeps_its_shape_around_what_it_resolves():
    tree = All(all=[due(Today(), Operator.LT), Condition(field="state", value="posted")])

    assert resolve(tree, on(MONDAY)) == All(
        all=[due(date(2026, 10, 12), Operator.LT), Condition(field="state", value="posted")]
    )


# ── in a store: the filter is read when it runs ────────────────────────────────


@dataclass
class Invoice(Entity):
    number: str
    owner_id: str = ""
    due: date = date(2026, 1, 1)
    posted_at: datetime = datetime(2026, 1, 1, tzinfo=UTC)


def invoices_over(days: list[int], owner: str = "u-7") -> AggregateRepository[Invoice]:
    repository = MemoryRepository()
    today = datetime.now(UTC).date()
    for ago in days:
        repository.save(
            Invoice(
                number=f"F-{ago}",
                owner_id=owner,
                due=today - timedelta(days=ago),
                posted_at=datetime.now(UTC) - timedelta(days=ago),
            )
        )
    return AggregateRepository(repository, Invoice)


def numbers(found) -> list[str]:
    return sorted(one.number for one in found)


def test_a_store_reads_today_when_it_runs():
    invoices = invoices_over([3, 0, -2])

    overdue = invoices.search(Criteria(where=due(Today(), Operator.LT)))

    assert numbers(overdue) == ["F-3"]


def test_the_same_filter_asked_later_means_another_day():
    invoices = invoices_over([3, 0, -2])
    asked = Criteria(where=due(Today(), Operator.LT))

    first = numbers(invoices.search(asked))
    after_a_day = Criteria(where=due(Today().after(days=1), Operator.LT))

    assert first == ["F-3"]
    assert numbers(invoices.search(after_a_day)) == ["F-0", "F-3"]


def test_a_date_against_an_instant_column_is_its_midnight():
    invoices = invoices_over([3, 0])
    since = Condition(field="posted_at", operator=Operator.GTE, value=Today().before(days=1))

    assert numbers(invoices.search(Criteria(where=since))) == ["F-0"]


def test_the_period_filters_a_store():
    invoices = invoices_over([0])
    asked = Condition(
        field="due", operator=Operator.BETWEEN, value=Period(name=Periods.THIS_MONTH)
    )

    assert numbers(invoices.search(Criteria(where=asked))) == ["F-0"]


def test_the_user_comes_from_the_servers_context_and_not_from_the_client():
    invoices = invoices_over([1])
    mine = Criteria(where=Condition(field="owner_id", value=FromContext(key="user_id")))

    with carrying({"user_id": "u-7"}):
        assert numbers(invoices.search(mine)) == ["F-1"]
    with carrying({"user_id": "someone-else"}):
        assert numbers(invoices.search(mine)) == []


def test_a_filter_that_names_a_context_key_nobody_set_is_refused_and_not_widened():
    invoices = invoices_over([1])
    mine = Criteria(where=Condition(field="owner_id", value=FromContext(key="user_id")))

    with carrying({}), pytest.raises(InvalidCriteria, match="the context has no 'user_id'"):
        invoices.search(mine)


def test_a_timezone_object_other_than_utc_is_accepted():
    plus_two = timezone(timedelta(hours=2))
    late = datetime(2026, 10, 12, 23, 0, tzinfo=UTC)

    assert read(Today(), late, timezone=plus_two) == date(2026, 10, 13)


def test_the_timezone_of_the_context_decides_the_day_a_store_reads():
    """UTC+14 and UTC-11 are 25 hours apart: whatever the moment, they are on different dates."""

    def day_in(zone: str) -> Any:
        with carrying({"timezone": zone}):
            read, _ = describe_class(Invoice).accept(due(Today()))
        assert isinstance(read, Condition)
        return read.value

    assert day_in("Pacific/Kiritimati") > day_in("Pacific/Pago_Pago")


def test_a_timezone_the_context_names_wrongly_is_refused():
    invoices = invoices_over([0])

    with carrying({"timezone": "Mars/Olympus"}), pytest.raises(
        ProgrammingError, match="Mars"
    ):
        invoices.search(Criteria(where=due(Today(), Operator.EQ)))


# ── a window of whole units ────────────────────────────────────────────────────


def windows(period: Period, now: datetime = MONDAY, **more) -> list[Any]:
    resolved = resolve(due(period, Operator.BETWEEN), on(now, **more))
    return [one.value for one in conditions_of(resolved)]


@pytest.mark.parametrize(
    ("window", "start", "end"),
    [
        (Period(unit=Grain.DAY, last=30), date(2026, 9, 12), date(2026, 10, 12)),
        (
            Period(unit=Grain.DAY, last=30, include_current=True),
            date(2026, 9, 13),
            date(2026, 10, 13),
        ),
        (Period(unit=Grain.DAY, next=7), date(2026, 10, 13), date(2026, 10, 20)),
        (
            Period(unit=Grain.DAY, next=7, include_current=True),
            date(2026, 10, 12),
            date(2026, 10, 19),
        ),
        (Period(unit=Grain.WEEK, last=1), date(2026, 10, 5), date(2026, 10, 12)),
        (Period(unit=Grain.MONTH, last=3), date(2026, 7, 1), date(2026, 10, 1)),
        (Period(unit=Grain.QUARTER, next=1), date(2027, 1, 1), date(2027, 4, 1)),
        (Period(unit=Grain.YEAR, last=2), date(2024, 1, 1), date(2026, 1, 1)),
    ],
)
def test_a_window_counts_whole_units_before_or_after_the_current_one(window, start, end):
    assert windows(window) == [start, end]


def test_a_window_is_written_without_what_it_leaves_out():
    window = Period(unit=Grain.DAY, last=30)

    assert window.model_dump(mode="json") == {"$": "period", "unit": "day", "last": 30}
    assert Period.model_validate(window.model_dump(mode="json")) == window


@pytest.mark.parametrize(
    "bad",
    [
        {"unit": "day"},
        {"unit": "day", "last": 3, "next": 3},
        {"last": 3},
        {"unit": "day", "last": 0},
        {"name": "this_month", "unit": "day", "last": 3},
        {"name": "this_month", "include_current": True},
    ],
)
def test_a_period_is_a_name_or_a_window_and_nothing_between(bad):
    with pytest.raises(InvalidCriteria):
        Period.model_validate({"$": "period", **bad})


# ── a column against another column ────────────────────────────────────────────


@dataclass
class Credit(Entity):
    code: str
    total: int = 0
    credit_limit: int = 0
    note: str = ""


def credits_with(*rows: tuple[str, int, int]) -> AggregateRepository[Credit]:
    repository = MemoryRepository()
    for code, total, limit in rows:
        repository.save(Credit(code=code, total=total, credit_limit=limit))
    return AggregateRepository(repository, Credit)


def test_a_column_is_compared_with_another_column_of_the_same_row():
    credits = credits_with(("a", 5, 10), ("b", 20, 10), ("c", 10, 10))
    over = Condition(
        field="total", operator=Operator.GT, value=FromField(name="credit_limit")
    )

    found = credits.search(Criteria(where=over))

    assert sorted(one.code for one in found) == ["b"]


def test_a_column_against_another_that_holds_nothing_is_not_true_as_in_sql():
    @dataclass
    class Limit(Entity):
        total: int = 0
        credit_limit: int | None = None

    repository = MemoryRepository()
    repository.save(Limit(total=5, credit_limit=None))
    repository.save(Limit(total=5, credit_limit=1))
    over = Condition(
        field="total", operator=Operator.GT, value=FromField(name="credit_limit")
    )

    found = AggregateRepository(repository, Limit).search(Criteria(where=over))

    assert [one.credit_limit for one in found] == [1]


def test_a_field_is_written_as_the_object_with_the_reserved_key():
    over = Condition(
        field="total", operator=Operator.GT, value=FromField(name="credit_limit")
    )

    written = over.model_dump(mode="json")

    assert written["value"] == {"$": "field", "name": "credit_limit"}
    assert Condition.model_validate(written) == over


@pytest.mark.parametrize(
    ("operator", "other", "reason"),
    [
        (Operator.GT, "nowhere", "unknown_field"),
        (Operator.GT, "note", "bad_value"),
        (Operator.IN, "credit_limit", "bad_value"),
        (Operator.LIKE, "credit_limit", "unsupported_operator"),
    ],
)
def test_a_column_against_another_is_dropped_and_reported_when_it_cannot_be_asked(
    operator, other, reason
):
    asked = Condition(field="total", operator=operator, value=FromField(name=other))

    kept, dropped = describe_class(Credit).accept(asked)

    assert kept is None and [one.reason for one in dropped] == [reason]


def test_a_field_is_not_read_before_the_row():
    over = Condition(
        field="total", operator=Operator.GT, value=FromField(name="credit_limit")
    )

    assert has_dynamic_values(over) is False


# ── a date against an instant column, in the context's timezone ───────────────


def test_a_date_against_an_instant_column_is_its_midnight_in_the_timezone():
    kind = FieldType.DATETIME

    assert kind.read(date(2026, 10, 12), LA_PAZ) == datetime(2026, 10, 12, 4, tzinfo=UTC)
    assert kind.read(date(2026, 10, 12)) == datetime(2026, 10, 12, tzinfo=UTC)


def test_a_store_reads_a_date_for_an_instant_column_in_the_timezone_of_the_context():
    def midnight_in(zone: str) -> Any:
        with carrying({"timezone": zone}):
            read, _ = describe_class(Invoice).accept(
                Condition(field="posted_at", operator=Operator.GTE, value=date(2026, 10, 12))
            )
        assert isinstance(read, Condition)
        return read.value

    assert midnight_in("America/La_Paz") == datetime(2026, 10, 12, 4, tzinfo=UTC)
    assert midnight_in("UTC") == datetime(2026, 10, 12, tzinfo=UTC)


# ── what the server offers ─────────────────────────────────────────────────────


def test_the_definition_publishes_the_dynamic_values_it_reads():
    published = {one.name: one for one in describe_class(Invoice).resolvers}

    assert set(published) == {"today", "now", "period", "context", "param", "field"}
    assert published["today"].returns == "date"
    assert "this_month" in published["period"].choices["name"]
    assert published["today"].choices["start_of"] == [one.value for one in Grain]


def test_a_memory_store_reads_a_group_filter_with_the_servers_context():
    repository = MemoryRepository()
    for number, owner, total in [("a", "ana", 100), ("b", "ana", 100), ("c", "beto", 10)]:
        repository.save(Invoice(number=number, owner_id=owner, due=date(2026, 1, 1)))
        repository.save(Credit(code=number, total=total))
    heavy = Grouping(
        group_by=(Level(field="code"),),
        measures={"total": Measure(function="sum", field="total")},
        where_measures=Condition(
            field="total", operator=Operator.GT, value=FromContext(key="floor")
        ),
    )

    with carrying({"floor": 50}):
        buckets = repository.group_by_levels(Credit, Criteria(grouping=heavy))

    assert sorted(str(one.value) for one in buckets) == ["a", "b"]
