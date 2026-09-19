"""That a cursor survives the trip, and that one from another ordering is refused.

The refusal is why the ordering travels inside the token: a client that changes the sort while
holding a cursor is asking a question with no answer, and answering it would produce a page
nobody could explain.

The datetime round trip is the other thing worth pinning — a key that came back as text would
compare lexicographically and move the page boundary.
"""

from datetime import date, datetime

import pytest

from sincpro_framework.ddd.criteria.pagination import CursorKeys
from sincpro_framework.ddd.exceptions import InvalidCriteria


def round_trip(keys: tuple, ordering: str = "-id") -> tuple:
    return CursorKeys.read(CursorKeys(keys=keys, ordering=ordering).token(), ordering).keys


def test_keys_come_back_as_they_went_in():
    assert round_trip(("ds_01a0", 42, None, True)) == ("ds_01a0", 42, None, True)


def test_a_datetime_comes_back_a_datetime():
    moment = datetime(2026, 3, 14, 15, 9, 26, 535000)

    restored = round_trip((moment, "ds_1"), "-at")

    assert restored == (moment, "ds_1")
    assert isinstance(restored[0], datetime)


def test_a_date_comes_back_a_date():
    day = date(2026, 3, 14)

    restored = round_trip((day,), "-on")

    assert restored == (day,)
    assert isinstance(restored[0], date)


def test_a_cursor_from_another_ordering_is_refused():
    token = CursorKeys(keys=("ds_1",), ordering="-registered_at").token()

    with pytest.raises(InvalidCriteria, match="different ordering"):
        CursorKeys.read(token, "-row_count")


def test_a_corrupted_token_says_so_rather_than_crashing():
    with pytest.raises(InvalidCriteria, match="not readable"):
        CursorKeys.read("not-a-cursor!!", "-id")


def test_the_token_carries_no_padding():
    """`=` is escaped by some clients and not others, which turns one cursor into two."""
    token = CursorKeys(keys=("a",), ordering="-id").token()

    assert "=" not in token
    assert CursorKeys.read(token, "-id").keys == ("a",)


def test_a_cursor_reads_its_keys_off_the_last_row():
    from dataclasses import dataclass

    from sincpro_framework.ddd.criteria import parse_order

    @dataclass
    class Row:
        row_id: str
        size: int

    cursor = CursorKeys.of(Row(row_id="r_9", size=3), parse_order("size,-row_id"))

    assert cursor.keys == (3, "r_9")
    assert cursor.ordering == "size,-row_id"
