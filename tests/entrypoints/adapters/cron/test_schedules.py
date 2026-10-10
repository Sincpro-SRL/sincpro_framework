"""When a cron fires: cron expressions in a timezone, and fixed intervals.

The cases that go wrong in production are the ones pinned here: day-of-month *or* day-of-week,
the spring-forward gap and the fall-back repeat, and a moment that is exactly on a tick.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from sincpro_framework.entrypoints.adapters.cron.domain import CronExpression, Every

LA_PAZ = ZoneInfo("America/La_Paz")
NEW_YORK = ZoneInfo("America/New_York")


def _local(tz: ZoneInfo, *parts: int) -> datetime:
    return datetime(*parts, tzinfo=tz)


def _next(cron: CronExpression, after: datetime, count: int = 1) -> list[datetime]:
    ticks, moment = [], after
    for _ in range(count):
        moment = cron.next_after(moment)
        ticks.append(moment)
    return ticks


# ---------------------------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------------------------


def test_every_day_at_two():
    cron = CronExpression("0 2 * * *", timezone="America/La_Paz")

    assert _next(cron, _local(LA_PAZ, 2026, 9, 26, 1, 59), 2) == [
        _local(LA_PAZ, 2026, 9, 26, 2, 0),
        _local(LA_PAZ, 2026, 9, 27, 2, 0),
    ]


def test_a_moment_exactly_on_a_tick_answers_the_next_one():
    cron = CronExpression("0 2 * * *", timezone="America/La_Paz")

    assert cron.next_after(_local(LA_PAZ, 2026, 9, 26, 2, 0)) == _local(
        LA_PAZ, 2026, 9, 27, 2, 0
    )


def test_steps_ranges_and_lists():
    cron = CronExpression("*/15 8-9 * * *", timezone="UTC")

    ticks = _next(cron, datetime(2026, 9, 26, 7, 59, tzinfo=UTC), 9)

    assert [(t.hour, t.minute) for t in ticks] == [
        (8, 0),
        (8, 15),
        (8, 30),
        (8, 45),
        (9, 0),
        (9, 15),
        (9, 30),
        (9, 45),
        (8, 0),
    ]


def test_weekdays_by_name_and_number():
    by_name = CronExpression("0 8 * * MON-FRI", timezone="UTC")
    by_number = CronExpression("0 8 * * 1-5", timezone="UTC")
    saturday = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)

    assert by_name.next_after(saturday) == by_number.next_after(saturday)
    assert by_name.next_after(saturday) == datetime(2026, 9, 28, 8, 0, tzinfo=UTC)  # Monday


def test_sunday_is_zero_or_seven():
    assert CronExpression("0 0 * * 0", timezone="UTC").next_after(
        datetime(2026, 9, 26, tzinfo=UTC)
    ) == CronExpression("0 0 * * 7", timezone="UTC").next_after(
        datetime(2026, 9, 26, tzinfo=UTC)
    )


def test_day_of_month_or_day_of_week_when_both_are_restricted():
    """The classic cron rule: `0 0 1 * MON` is the 1st of the month *and* every Monday."""
    cron = CronExpression("0 0 1 * MON", timezone="UTC")

    ticks = _next(cron, datetime(2026, 9, 26, tzinfo=UTC), 3)

    assert [t.date().isoformat() for t in ticks] == ["2026-09-28", "2026-10-01", "2026-10-05"]


def test_months_by_name():
    cron = CronExpression("0 0 1 JAN,JUL *", timezone="UTC")

    assert cron.next_after(datetime(2026, 9, 26, tzinfo=UTC)) == datetime(
        2027, 1, 1, tzinfo=UTC
    )


@pytest.mark.parametrize(
    "expression",
    [
        "",
        "* * * *",
        "60 * * * *",
        "* 24 * * *",
        "* * 0 * *",
        "* * * 13 *",
        "* * * * 8",
        "*/0 * * * *",
        "a * * * *",
    ],
)
def test_a_malformed_expression_is_refused_when_declared(expression: str):
    with pytest.raises(ValueError, match="cron"):
        CronExpression(expression, timezone="UTC")


def test_an_unknown_timezone_is_refused_when_declared():
    with pytest.raises(ValueError, match="timezone"):
        CronExpression("0 2 * * *", timezone="Mars/Olympus")


# ---------------------------------------------------------------------------------------------
# Daylight saving time
# ---------------------------------------------------------------------------------------------


def test_a_tick_in_the_spring_forward_gap_runs_at_the_next_valid_instant():
    """2026-03-08 in New York jumps from 02:00 to 03:00: 02:30 does not exist."""
    cron = CronExpression("30 2 * * *", timezone="America/New_York")

    tick = cron.next_after(_local(NEW_YORK, 2026, 3, 8, 0, 0))

    assert tick.astimezone(UTC) == datetime(2026, 3, 8, 7, 30, tzinfo=UTC)  # 03:30 EDT


def test_a_tick_in_the_fall_back_repeat_runs_once():
    """2026-11-01 in New York repeats 01:00–02:00: 01:30 happens twice, the job runs once."""
    cron = CronExpression("30 1 * * *", timezone="America/New_York")

    first = cron.next_after(_local(NEW_YORK, 2026, 11, 1, 0, 0))
    after_first = cron.next_after(first)

    assert first.astimezone(UTC) == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)  # 01:30 EDT
    assert after_first.date().isoformat() == "2026-11-02"


def test_ticks_come_back_in_utc_order_and_aware():
    cron = CronExpression("0 * * * *", timezone="America/New_York")

    ticks = _next(cron, _local(NEW_YORK, 2026, 11, 1, 0, 0), 4)

    assert all(t.tzinfo is not None for t in ticks)
    assert [t.astimezone(UTC) for t in ticks] == sorted(t.astimezone(UTC) for t in ticks)


# ---------------------------------------------------------------------------------------------
# Every
# ---------------------------------------------------------------------------------------------


def test_every_ticks_on_multiples_of_its_interval_whatever_the_moment_given():
    every = Every(timedelta(minutes=15))

    assert every.next_after(datetime(2026, 9, 26, 10, 7, 3, tzinfo=UTC)) == datetime(
        2026, 9, 26, 10, 15, tzinfo=UTC
    )
    assert every.next_after(datetime(2026, 9, 26, 10, 15, tzinfo=UTC)) == datetime(
        2026, 9, 26, 10, 30, tzinfo=UTC
    )


def test_every_refuses_a_zero_or_negative_interval():
    with pytest.raises(ValueError, match="interval"):
        Every(timedelta(0))


def test_a_naive_moment_is_refused():
    with pytest.raises(ValueError, match="aware"):
        CronExpression("0 2 * * *", timezone="UTC").next_after(datetime(2026, 9, 26))
