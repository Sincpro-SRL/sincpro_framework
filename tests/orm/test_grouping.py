"""Counting a set by what its rows share, one level at a time.

A grouping is not a page of records: it answers "how many of each", and the rows are reached by
opening a bucket. That is why every bucket carries the criteria that opens it — the test that
checks it is what keeps a client from rebuilding that filter by hand and drifting from it.
"""

from datetime import datetime

import pytest

from sincpro_framework.ddd.criteria import (
    Condition,
    Criteria,
    Fold,
    Grouping,
    Level,
    Operator,
    conditions_of,
)
from sincpro_framework.ddd.exceptions import InvalidCriteria
from sincpro_framework.orm.sqlalchemy.sql_translator import bucket_range

from .models import Things

BY_OWNER = Grouping(by=(Level(field="owner"),))


def test_one_level_counts_every_bucket(store):
    buckets = store.group_by_levels(Things, Criteria(grouping=BY_OWNER))

    assert [one.field for one in buckets] == ["owner"] * len(buckets)
    assert sum(one.count for one in buckets) == len(store.search(Things).items)


def test_a_bucket_carries_the_criteria_that_opens_it(store):
    """**What makes opening a bucket not a new API.** The criteria already comes merged with
    the filter in force, so searching with it pages inside the bucket like any other reading.
    """
    bucket = store.group_by_levels(Things, Criteria(grouping=BY_OWNER))[0]

    page = store.search(Things, bucket.criteria)

    assert page.count is not None and page.count.value == bucket.count


def test_the_outer_filter_still_holds_inside_the_bucket(store):
    large = Criteria(
        where=Condition(field="size", operator=Operator.GTE, value=10),
        grouping=BY_OWNER,
    )

    buckets = store.group_by_levels(Things, large)

    for bucket in buckets:
        page = store.search(Things, bucket.criteria)
        assert all(one.size >= 10 for one in page.items)


def test_folds_are_asked_for_by_name(store):
    with_total = Criteria(
        grouping=Grouping(
            by=(Level(field="owner"),), totals={"weight": Fold(function="sum", field="size")}
        )
    )

    buckets = store.group_by_levels(Things, with_total)

    for bucket in buckets:
        page = store.search(Things, bucket.criteria)
        assert bucket.totals["weight"] == sum(one.size for one in page.items)


def test_a_second_level_arrives_only_when_depth_reaches_it(store):
    two = Grouping(by=(Level(field="owner"), Level(field="size")), depth=2)
    one = Grouping(by=(Level(field="owner"), Level(field="size")), depth=1)

    nested = store.group_by_levels(Things, Criteria(grouping=two))
    flat = store.group_by_levels(Things, Criteria(grouping=one))

    assert any(bucket.groups for bucket in nested)
    assert all(bucket.groups == [] for bucket in flat)
    for bucket in nested:
        assert sum(one.count for one in bucket.groups) == bucket.count


def test_grouping_by_nothing_is_refused(store):
    """A bucket holding everything is a count, not a grouping."""
    with pytest.raises(InvalidCriteria, match="at least one field"):
        store.group_by_levels(Things, Criteria())


def test_grouping_by_a_field_that_does_not_exist_is_refused(store):
    """Unlike a filter, this is NOT dropped in silence: a grouping by a missing field does not
    answer less, it answers something else."""
    with pytest.raises(InvalidCriteria):
        store.group_by_levels(Things, Criteria(grouping=Grouping(by=(Level(field="nope"),))))


def test_a_date_is_grouped_at_the_grain_asked_for(store):
    """Without this, grouping a catalogue by date gives one bucket per row: a listing with
    extra steps."""
    monthly = Grouping(by=(Level(field="made_at", grain="month"),))

    buckets = store.group_by_levels(Things, Criteria(grouping=monthly))

    assert all(len(str(one.value)) == len("2026-09") for one in buckets)
    assert len(buckets) < store.count(Things).value


def test_a_grain_that_does_not_exist_is_refused_naming_the_ones_that_do(store):
    odd_grain = Grouping(by=(Level(field="made_at", grain="fortnight"),))

    with pytest.raises(InvalidCriteria, match="granularity"):
        store.group_by_levels(Things, Criteria(grouping=odd_grain))


def test_a_level_and_a_fold_already_built_pass_through():
    """Written this way from Python; reading them from JSON is the other half."""
    assert Level.read(Level(field="owner")) == Level(field="owner")
    assert Fold.read(Fold(function="sum", field="size")) == Fold(function="sum", field="size")
    assert Level.read({"field": "made_at", "grain": "month"}).grain == "month"
    assert Fold.read({"function": "max", "field": "size"}).function == "max"


def test_opening_a_date_bucket_returns_that_bucket(store):
    """**The bug this covers answered more, in silence.** The value of a cut bucket is what
    `strftime` gave — the text "2026" — and the criteria compared it for equality against a
    date column: the model dropped it as an unreadable value, the condition vanished, and
    opening the bucket brought the whole catalogue while the count said otherwise."""
    monthly = Grouping(by=(Level(field="made_at", grain="month"),))

    for bucket in store.group_by_levels(Things, Criteria(grouping=monthly)):
        page = store.search(Things, bucket.criteria)

        assert page.dropped == ()
        assert page.count is not None and page.count.value == bucket.count


def test_a_date_bucket_opens_with_a_half_open_range(store):
    """The end stays out: with an inclusive range, a row saved at 00:00 on the first of the
    next month would fall in two buckets and the levels would stop adding up."""
    bucket = store.group_by_levels(
        Things, Criteria(grouping=Grouping(by=(Level(field="made_at", grain="year"),)))
    )[0]

    leaves = conditions_of(bucket.criteria.expression)

    assert [one.operator for one in leaves] == [Operator.GTE, Operator.LT]


def test_date_levels_add_up_to_what_the_parent_says(store):
    """If one level's range overlapped the next one's, this would not close."""
    nested = Grouping(
        by=(Level(field="made_at", grain="year"), Level(field="made_at", grain="month")),
        depth=2,
    )

    for bucket in store.group_by_levels(Things, Criteria(grouping=nested)):
        assert sum(one.count for one in bucket.groups) == bucket.count


@pytest.mark.parametrize(
    "grain, value, start, end",
    [
        ("year", "2026", datetime(2026, 1, 1), datetime(2027, 1, 1)),
        ("month", "2026-09", datetime(2026, 9, 1), datetime(2026, 10, 1)),
        # December crosses the year: `(12 % 12) + 1` gives January, and the year adds one.
        ("month", "2026-12", datetime(2026, 12, 1), datetime(2027, 1, 1)),
        ("week", "2026-W37", datetime(2026, 9, 14), datetime(2026, 9, 21)),
        ("day", "2026-09-13", datetime(2026, 9, 13), datetime(2026, 9, 14)),
    ],
)
def test_each_grain_says_where_its_bucket_starts_and_ends(grain, value, start, end):
    assert bucket_range(grain, value) == (start, end)


def test_the_end_of_a_bucket_is_the_start_of_the_next():
    """Half open, and that is why the counts of the levels add up to the parent's: with an
    inclusive range, a row saved at 00:00 on the first of the next month would fall in two."""
    _, end = bucket_range("month", "2026-09")
    start, _ = bucket_range("month", "2026-10")

    assert end == start


def test_a_range_grain_that_does_not_exist_is_refused_too():
    with pytest.raises(InvalidCriteria, match="granularity"):
        bucket_range("fortnight", "2026-Q3")
