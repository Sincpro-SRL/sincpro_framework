"""A cursor carries every type a sortable column can hold, and comes back as that type."""

from datetime import date, datetime
from decimal import Decimal

from sincpro_framework.ddd.pagination import CursorKeys


def test_a_cursor_carries_decimals_dates_and_datetimes_and_gives_them_back_as_typed():
    keys = (Decimal("1.50"), datetime(2026, 3, 14, 15, 9), date(2026, 3, 14), "th_9", 3)

    token = CursorKeys(keys=keys, ordering="-debit,id").token()

    assert CursorKeys.read(token, "-debit,id").keys == keys
