"""When a cron fires: a cron expression read in a timezone, or a fixed interval.

    CronExpression("0 2 * * *", timezone="America/La_Paz")    every day at 02:00, La Paz time
    CronExpression("*/15 8-18 * * MON-FRI", timezone="UTC")   every quarter hour, office hours
    Every(timedelta(minutes=15))                               at :00, :15, :30 and :45, UTC

Context: the standard five fields — minute, hour, day of month, month, day of week — with `*`,
lists, ranges, steps and names. A timezone is required: a cron that says "02:00" means 02:00
somewhere. Daylight saving is evaluated in that zone: a tick inside the spring-forward gap runs
shifted past the gap (02:30 on a day that jumps from 02:00 to 03:00 runs at 03:30), and the hour
that happens twice on fall back is counted once — its first occurrence.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
WEEKDAYS = ("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT")
SEARCH_YEARS = 5
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _refuse_naive(moment: datetime) -> None:
    if moment.tzinfo is None:
        raise ValueError(f"{moment} is naive: a cron compares aware datetimes only")


def _number(token: str, names: tuple[str, ...], offset: int) -> int:
    upper = token.upper()
    if upper in names:
        return names.index(upper) + offset
    if not token.isdigit():
        raise ValueError(f"cron: {token!r} is neither a number nor a name")
    return int(token)


def _field(
    text: str, low: int, high: int, names: tuple[str, ...] = (), offset: int = 0
) -> frozenset[int]:
    values: set[int] = set()
    for part in text.split(","):
        base, has_step, step_text = part.partition("/")
        step = 1
        if has_step:
            if not step_text.isdigit() or int(step_text) < 1:
                raise ValueError(f"cron: step in {part!r} must be a positive number")
            step = int(step_text)
        if base == "*":
            start, end = low, high
        elif "-" in base:
            first, _, last = base.partition("-")
            start, end = _number(first, names, offset), _number(last, names, offset)
        else:
            start = _number(base, names, offset)
            end = high if step_text else start
        if not (low <= start <= end <= high):
            raise ValueError(f"cron: {part!r} is outside {low}-{high}")
        values.update(range(start, end + 1, step))
    return frozenset(values)


class Trigger(Protocol):
    def next_after(self, moment: datetime) -> datetime:
        """The first tick strictly after `moment`, timezone-aware."""
        ...


class CronExpression:
    """`CronExpression("0 2 * * *", timezone="America/La_Paz")`: every day at 02:00, La Paz time."""

    def __init__(self, expression: str, timezone: str) -> None:
        parts = expression.split()
        if len(parts) != 5:
            raise ValueError(f"cron: {expression!r} needs five fields, it has {len(parts)}")
        try:
            zone = ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError(f"unknown timezone {timezone!r}") from error
        minute, hour, day, month, weekday = parts
        self.expression = expression
        self.timezone = timezone
        self._zone = zone
        self._minutes = sorted(_field(minute, 0, 59))
        self._hours = sorted(_field(hour, 0, 23))
        self._days = _field(day, 1, 31)
        self._months = _field(month, 1, 12, MONTHS, offset=1)
        self._weekdays = frozenset(
            0 if one == 7 else one for one in _field(weekday, 0, 7, WEEKDAYS)
        )
        self._any_day = day.startswith("*")
        self._any_weekday = weekday.startswith("*")

    def __str__(self) -> str:
        return f"{self.expression} ({self.timezone})"

    def _runs_on(self, day: date) -> bool:
        """Context: when both day fields are restricted, either one matching is enough — the
        rule every cron implementation follows; when one is `*`, only the other counts."""
        if day.month not in self._months:
            return False
        by_day = day.day in self._days
        by_weekday = (day.isoweekday() % 7) in self._weekdays
        if self._any_day or self._any_weekday:
            return by_day and by_weekday
        return by_day or by_weekday

    def _instant(self, day: date, hour: int, minute: int) -> datetime:
        """The wall time in this zone as an instant. A wall time that does not exist (spring
        forward) is read with the offset before the jump, which lands after the gap; one that
        happens twice (fall back) is its first occurrence."""
        wall = datetime(day.year, day.month, day.day, hour, minute, tzinfo=self._zone)
        return wall.astimezone(UTC).astimezone(self._zone)

    def next_after(self, moment: datetime) -> datetime:
        _refuse_naive(moment)
        start = moment.astimezone(self._zone).date()
        for offset in range(366 * SEARCH_YEARS):
            day = start + timedelta(days=offset)
            if not self._runs_on(day):
                continue
            for hour in self._hours:
                for minute in self._minutes:
                    instant = self._instant(day, hour, minute)
                    if instant > moment:
                        return instant
        raise ValueError(f"cron: {self} has no tick in the next {SEARCH_YEARS} years")


class Every:
    """Context: ticks fall on multiples of `interval` counted from the Unix epoch, so every
    replica computes the same ticks whenever it started — and claims the same one."""

    def __init__(self, interval: timedelta) -> None:
        if interval <= timedelta(0):
            raise ValueError(f"interval must be positive, got {interval}")
        self.interval = interval

    def __str__(self) -> str:
        return f"every {self.interval}"

    def next_after(self, moment: datetime) -> datetime:
        _refuse_naive(moment)
        return EPOCH + ((moment - EPOCH) // self.interval + 1) * self.interval
