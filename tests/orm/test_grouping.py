"""Counting a set by what its rows share, one level at a time.

A grouping is not a page of records: it answers "how many of each", and the rows are reached by
opening a bucket. That is why every bucket carries the criteria that opens it — the test that
checks it is what keeps a client from rebuilding that filter by hand and drifting from it.
"""

from datetime import datetime

import pytest

from sincpro_framework.ddd.criteria import (
    All,
    Any_,
    Condition,
    CountMode,
    Criteria,
    Grouping,
    Level,
    Measure,
    Not,
    Operator,
    Sort,
    conditions_of,
    parse_order,
)
from sincpro_framework.ddd.criteria.pagination import Pagination
from sincpro_framework.ddd.entity.entity_collection import Count
from sincpro_framework.ddd.exceptions import InvalidCriteria
from sincpro_framework.orm.sqlalchemy.sql_translator import bucket_range

from .models import Things

BY_OWNER = Grouping(group_by=(Level(field="owner"),))


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
            group_by=(Level(field="owner"),),
            measures={"weight": Measure(function="sum", field="size")},
        )
    )

    buckets = store.group_by_levels(Things, with_total)

    for bucket in buckets:
        page = store.search(Things, bucket.criteria)
        assert bucket.measures["weight"] == sum(one.size for one in page.items)


def test_every_level_named_is_answered_and_nothing_below_the_last(store):
    """Two fields are two levels, always; a screen that wants one level at a time names one
    and opens a bucket with a grouping of its own."""
    two = Grouping(group_by=(Level(field="owner"), Level(field="size")))
    one = Grouping(group_by=(Level(field="owner"),))

    nested = store.group_by_levels(Things, Criteria(grouping=two))
    flat = store.group_by_levels(Things, Criteria(grouping=one))

    assert all(bucket.groups for bucket in nested)
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
        store.group_by_levels(
            Things, Criteria(grouping=Grouping(group_by=(Level(field="nope"),)))
        )


def test_a_date_is_grouped_at_the_grain_asked_for(store):
    """Without this, grouping a catalogue by date gives one bucket per row: a listing with
    extra steps."""
    monthly = Grouping(group_by=(Level(field="made_at", grain="month"),))

    buckets = store.group_by_levels(Things, Criteria(grouping=monthly))

    assert all(len(str(one.value)) == len("2026-09") for one in buckets)
    assert len(buckets) < store.count(Things).value


def test_a_grain_that_does_not_exist_is_refused_naming_the_ones_that_do(store):
    odd_grain = Grouping(group_by=(Level(field="made_at", grain="fortnight"),))

    with pytest.raises(InvalidCriteria, match="granularity"):
        store.group_by_levels(Things, Criteria(grouping=odd_grain))


def test_a_level_and_a_fold_already_built_pass_through():
    """Written this way from Python; reading them from JSON is the other half."""
    assert Level.read(Level(field="owner")) == Level(field="owner")
    assert Measure.read(Measure(function="sum", field="size")) == Measure(
        function="sum", field="size"
    )
    assert Level.read({"field": "made_at", "grain": "month"}).grain == "month"
    assert Measure.read({"function": "max", "field": "size"}).function == "max"


def test_opening_a_date_bucket_returns_that_bucket(store):
    """**The bug this covers answered more, in silence.** The value of a cut bucket is what
    `strftime` gave — the text "2026" — and the criteria compared it for equality against a
    date column: the model dropped it as an unreadable value, the condition vanished, and
    opening the bucket brought the whole catalogue while the count said otherwise."""
    monthly = Grouping(group_by=(Level(field="made_at", grain="month"),))

    for bucket in store.group_by_levels(Things, Criteria(grouping=monthly)):
        page = store.search(Things, bucket.criteria)

        assert page.dropped == ()
        assert page.count is not None and page.count.value == bucket.count


def test_a_date_bucket_opens_with_a_half_open_range(store):
    """The end stays out: with an inclusive range, a row saved at 00:00 on the first of the
    next month would fall in two buckets and the levels would stop adding up."""
    bucket = store.group_by_levels(
        Things, Criteria(grouping=Grouping(group_by=(Level(field="made_at", grain="year"),)))
    )[0]

    leaves = conditions_of(bucket.criteria.expression)

    assert [one.operator for one in leaves] == [Operator.GTE, Operator.LT]


def test_date_levels_add_up_to_what_the_parent_says(store):
    """If one level's range overlapped the next one's, this would not close."""
    nested = Grouping(
        group_by=(
            Level(field="made_at", grain="year"),
            Level(field="made_at", grain="month"),
        ),
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
        ("week", "2026-W37", datetime(2026, 9, 7), datetime(2026, 9, 14)),  # ISO week
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


# ── a page of ids per group ──────────────────────────────────────────────────


def paged_by_owner(limit: int = 2) -> Criteria:
    return Criteria(
        order=parse_order("size,thing_id"),
        pagination=Pagination(limit=limit),
        grouping=BY_OWNER,
    )


def test_without_a_page_asked_a_bucket_carries_no_ids(store):
    buckets = store.group_by_levels(Things, Criteria(grouping=BY_OWNER))

    assert all(bucket.ids == [] and bucket.cursor is None for bucket in buckets)


def test_with_a_page_asked_every_group_carries_its_first_ids_in_order(store, things):
    """`grouping` with a page is a page per group: the first two ids of every owner, in the
    criteria's order, an exact count, and a cursor because there is more."""
    buckets = store.group_by_levels(Things, paged_by_owner(limit=2))

    for bucket in buckets:
        mine = sorted(
            (t for t in things if t.owner == bucket.value), key=lambda t: (t.size, t.thing_id)
        )
        assert bucket.ids == [t.thing_id for t in mine[:2]]
        assert bucket.count == len(mine) and bucket.count > 2
        assert bucket.cursor is not None


def test_a_group_is_opened_with_browse_in_the_same_order(store):
    bucket = store.group_by_levels(Things, paged_by_owner(limit=3))[0]

    opened = store.browse(Things, bucket.ids)

    assert opened.ids == bucket.ids


def test_a_group_goes_on_with_its_own_cursor_without_repeating(store, things):
    """The bucket's criteria already holds the group's filter, order and limit; the cursor
    makes it the next page inside that group and nothing else."""
    bucket = store.group_by_levels(Things, paged_by_owner(limit=2))[1]

    rest = store.search(Things, bucket.criteria.resuming_from(bucket.cursor))

    mine = sorted(
        (t for t in things if t.owner == bucket.value), key=lambda t: (t.size, t.thing_id)
    )
    assert rest.ids == [t.thing_id for t in mine[2:4]]
    assert not set(rest.ids) & set(bucket.ids)


def test_a_group_with_fewer_rows_than_the_page_has_no_cursor(store):
    buckets = store.group_by_levels(Things, paged_by_owner(limit=50))

    assert all(len(b.ids) == b.count and b.cursor is None for b in buckets)


def test_ids_live_on_the_deepest_level_only(store):
    two = Grouping(group_by=(Level(field="owner"), Level(field="size")))
    asked = Criteria(
        order=parse_order("thing_id"), pagination=Pagination(limit=1), grouping=two
    )

    for owner in store.group_by_levels(Things, asked):
        assert owner.ids == []
        assert all(len(size.ids) == min(1, size.count) for size in owner.groups)
        assert sum(size.count for size in owner.groups) == owner.count


def test_a_page_per_group_costs_one_statement_more(store, queries_run):
    with queries_run() as statements:
        store.group_by_levels(Things, paged_by_owner(limit=2))

    assert len(statements) == 2  # the level, and the page of ids of every group in it


def test_a_search_with_a_grouping_and_a_page_answers_a_page_per_group(store, things):
    """What the other side of a relation receives: `limit` rows for EVERY owner, not for the
    set, ordered inside each, in one statement."""
    page = store.search(Things, paged_by_owner(limit=2))

    per_owner = {}
    for thing in page:
        per_owner.setdefault(thing.owner, []).append(thing)
    assert set(per_owner) == {t.owner for t in things}
    assert all(len(mine) == 2 for mine in per_owner.values())
    assert all(
        mine == sorted(mine, key=lambda t: (t.size, t.thing_id))
        for mine in per_owner.values()
    )
    assert page.cursor is None and page.count == Count(value=len(page), exact=True)


# ── groups that filter, order and page ───────────────────────────────────────


def test_having_filters_the_groups_the_rows_made(store, things):
    """`where` filters the rows; `having` filters what those rows added up to."""
    crowded = Criteria(
        grouping=Grouping(
            group_by=(Level(field="owner"),),
            where_measures=Condition(field="count", value=8, operator=Operator.GT),
        )
    )

    buckets = store.group_by_levels(Things, crowded)

    by_owner = {
        owner: len([t for t in things if t.owner == owner])
        for owner in {t.owner for t in things}
    }
    assert {bucket.value for bucket in buckets} == {o for o, n in by_owner.items() if n > 8}
    assert all(bucket.count > 8 for bucket in buckets)


def test_having_reads_what_the_groups_folded(store, things):
    heavy = Criteria(
        grouping=Grouping(
            group_by=(Level(field="owner"),),
            measures={"weight": Measure(function="sum", field="size")},
            where_measures=Condition(field="weight", value=15, operator=Operator.GT),
        )
    )

    buckets = store.group_by_levels(Things, heavy)

    assert buckets and all(bucket.measures["weight"] > 15 for bucket in buckets)


def test_having_over_something_no_group_folded_is_refused(store):
    with pytest.raises(InvalidCriteria, match="is not something a group folded"):
        store.group_by_levels(
            Things,
            Criteria(
                grouping=Grouping(
                    group_by=(Level(field="owner"),),
                    where_measures=Condition(field="size", value=1, operator=Operator.GT),
                )
            ),
        )


def test_groups_come_back_in_the_order_asked(store, things):
    asked = Criteria(
        grouping=Grouping(
            group_by=(Level(field="owner"),),
            measures={"weight": Measure(function="sum", field="size")},
            order=parse_order("-weight"),
        )
    )

    buckets = store.group_by_levels(Things, asked)

    weights = [bucket.measures["weight"] for bucket in buckets]
    assert weights == sorted(weights, reverse=True)
    assert len(buckets) == len({t.owner for t in things})


def test_groups_can_be_ordered_by_how_many_they_hold(store):
    buckets = store.group_by_levels(
        Things,
        Criteria(
            grouping=Grouping(
                group_by=(Level(field="size"),), order=parse_order("-count,size")
            )
        ),
    )

    counts = [bucket.count for bucket in buckets]
    assert counts == sorted(counts, reverse=True)


def test_ordering_groups_by_something_that_is_not_one_is_refused(store):
    with pytest.raises(InvalidCriteria, match="ordered by a level"):
        store.group_by_levels(
            Things,
            Criteria(
                grouping=Grouping(group_by=(Level(field="owner"),), order=parse_order("name"))
            ),
        )


def test_a_page_of_groups_answers_the_first_ones_and_no_more(store, things):
    top = Criteria(
        grouping=Grouping(
            group_by=(Level(field="size"),),
            order=parse_order("-count,size"),
            pagination=Pagination(limit=2),
        )
    )

    buckets = store.group_by_levels(Things, top)
    every = store.group_by_levels(
        Things,
        Criteria(
            grouping=Grouping(
                group_by=(Level(field="size"),), order=parse_order("-count,size")
            )
        ),
    )

    assert len(buckets) == 2 and len(every) == len({t.size for t in things})
    assert [b.value for b in buckets] == [b.value for b in every[:2]]


def test_a_page_of_groups_carries_the_levels_below_it_and_nothing_else(store, queries_run):
    """The deeper level answers for the groups that survived the page, so the page saves what
    it was asked to save."""
    top = Criteria(
        grouping=Grouping(
            group_by=(Level(field="owner"), Level(field="size")),
            order=parse_order("-count"),
            pagination=Pagination(limit=1),
        )
    )

    with queries_run() as statements:
        buckets = store.group_by_levels(Things, top)

    assert len(buckets) == 1 and buckets[0].groups
    assert sum(one.count for one in buckets[0].groups) == buckets[0].count
    assert len(statements) == 2
    assert "IN (" in statements[1] or "IS NULL" in statements[1].upper()


def test_one_reading_says_everything_at_once(store):
    """**The complex case, end to end.** Ten things asked in one object: a filter with AND, OR
    and NOT over four fields; the order and the page of the ROWS; two grouping levels, the
    second cutting a date; four measures; a filter over what those measures answered; and the
    order and the page of the GROUPS.

    It is one test because the claim is that they compose — each part is proven alone
    elsewhere, and what could break here is one part quietly overruling another.
    """
    reading = Criteria(
        # the rows
        where=All(
            all=[
                Condition(field="size", operator=Operator.BETWEEN, value=[1, 1000]),
                Any_(
                    any=[
                        Condition(field="tags", operator=Operator.CONTAINS, value="even"),
                        Condition(field="size", operator=Operator.GTE, value=10),
                    ]
                ),
                Not(negate=Condition(field="owner", operator=Operator.IS_NULL, value=True)),
            ]
        ),
        order=parse_order("-size,thing_id"),
        pagination=Pagination(limit=3),
        # the groups
        grouping=Grouping(
            group_by=(Level(field="owner"), Level(field="made_at", grain="month")),
            measures={
                "total": Measure(function="sum", field="size"),
                "biggest": Measure(function="max", field="size"),
                "average": Measure(function="avg", field="size"),
                "named": Measure(function="count", field="name"),
            },
            where_measures=Condition(field="count", operator=Operator.GTE, value=1),
            order=(Sort(field="total", descending=True),),
            pagination=Pagination(limit=5),
        ),
        count=CountMode.EXACT,
    )

    buckets = store.group_by_levels(Things, reading)

    # the groups: at most the page asked for, ordered by what they measured, none empty
    assert 0 < len(buckets) <= 5
    assert [one.field for one in buckets] == ["owner"] * len(buckets)
    totals = [one.measures["total"] for one in buckets]
    assert totals == sorted(totals, reverse=True), "ordered by -total"
    for bucket in buckets:
        assert bucket.count >= 1, "where_measures kept only the groups that answered"
        assert bucket.measures["biggest"] >= bucket.measures["average"]
        assert bucket.measures["named"] == bucket.count, "every thing has a name"

        # the rows' page reaches inside every bucket: three ids, in the rows' order
        assert len(bucket.ids) <= 3
        inside = store.browse(Things, bucket.ids)
        assert [one.thing_id for one in inside] == bucket.ids
        assert [one.size for one in inside] == sorted(
            (one.size for one in inside), reverse=True
        ), "the rows' order decides which rows a bucket shows first"

        # the outer filter still holds in there, and the bucket's own value too
        for one in inside:
            assert one.owner == bucket.value
            assert one.owner is not None and 1 <= one.size <= 1000

        # the level below only answers for this bucket's rows
        assert sum(inner.count for inner in bucket.groups) == bucket.count
        for inner in bucket.groups:
            assert inner.field == "made_at"

    # and opening a bucket is a plain search with the criteria it carries
    opened = store.search(Things, buckets[0].criteria)
    assert opened.count is not None and opened.count.value == buckets[0].count


def test_the_two_orders_and_the_two_pages_do_not_overrule_each_other(store):
    """`order`/`pagination` are the rows'; `grouping.order`/`grouping.pagination` are the
    groups'. Same words, one level down, and neither reaches the other.
    """
    reading = Criteria(
        order=parse_order("size,thing_id"),
        pagination=Pagination(limit=2),
        grouping=Grouping(
            group_by=(Level(field="owner"),),
            measures={"total": Measure(function="sum", field="size")},
            order=(Sort(field="total", descending=True),),
            pagination=Pagination(limit=1),
        ),
    )

    buckets = store.group_by_levels(Things, reading)

    assert len(buckets) == 1, "the groups' page: one bucket"
    assert len(buckets[0].ids) == 2, "the rows' page: two ids inside it"

    ascending = store.browse(Things, buckets[0].ids)
    assert [one.size for one in ascending] == sorted(one.size for one in ascending)


class TestTheTwoMeasuresThatAreNotAPlainFunction:
    """`count_distinct` and `percentile` do not compile to `func.<name>(column)`, and one of
    them is not available on every engine. Both facts are the test."""

    def test_count_distinct_counts_the_values_once_each(self, store):
        reading = Criteria(
            grouping=Grouping(
                group_by=(Level(field="owner"),),
                measures={
                    "rows": Measure(function="count", field="thing_id"),
                    "sizes": Measure(function="count_distinct", field="size"),
                },
            )
        )

        for bucket in store.group_by_levels(Things, reading):
            page = store.search(Things, bucket.criteria)
            assert bucket.measures["sizes"] == len({one.size for one in page.items})
            assert bucket.measures["sizes"] <= bucket.measures["rows"]

    def test_a_percentile_is_refused_by_name_on_an_engine_that_cannot_compute_it(self, store):
        """SQLite has no `percentile_cont`. Refusing here says which engine could not; letting
        it through would come back as "no such function" from somewhere else entirely."""
        reading = Criteria(
            grouping=Grouping(
                group_by=(Level(field="owner"),),
                measures={
                    "middle": Measure(function="percentile", field="size", argument=0.5)
                },
            )
        )

        with pytest.raises(InvalidCriteria, match="does not compute percentiles"):
            store.group_by_levels(Things, reading)

    def test_a_measure_that_takes_no_argument_refuses_one(self):
        with pytest.raises(InvalidCriteria, match="takes no argument"):
            Measure(function="sum", field="size", argument=0.5)

    def test_a_percentile_without_its_fraction_is_not_a_percentile(self):
        with pytest.raises(InvalidCriteria, match="between 0 and 1"):
            Measure(function="percentile", field="size")
