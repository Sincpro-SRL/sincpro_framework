"""The engine against a real database, and the four promises that are numbers.

Most of what this design claims is not about what comes back — it is about *how much work* it
took and *whether the metadata is true*. Those are the failures that survive a code review: a
page that silently loses rows, a total that is a fragment, a relation that starts loading per
row. So the assertions here are counts and invariants rather than shapes.

The walk tests are the ones to keep. Paging through everything and asserting each record
appears exactly once is the property `OFFSET` fails and a keyset passes, and running the same
walk with a row inserted halfway is the case that separates them.
"""

import pytest

from sincpro_framework.ddd.criteria import (
    Condition,
    CountMode,
    Criteria,
    Operator,
    parse_order,
)
from sincpro_framework.ddd.exceptions import InvalidCriteria
from sincpro_framework.ddd.pagination import Offset, Pagination
from sincpro_framework.ddd.repository import Repository as RepositoryProtocol

from .models import ROW_COUNT, Thing, Things, a_thing


def walk(store, criteria: Criteria) -> list[str]:
    """Every id the pages yield, in order, following the cursors to the end."""
    seen: list[str] = []
    page = store.search(Things, criteria)
    while True:
        seen.extend(page.ids)
        if page.cursor is None:
            return seen
        page = store.search(Things, criteria.resuming_from(page.cursor))


def test_a_search_hands_back_the_collection_class_it_was_given(store):
    assert type(store.search(Things, Criteria(pagination=Pagination(limit=3)))) is Things
    assert (
        type(store.search(Thing, Criteria(pagination=Pagination(limit=3)))).__name__
        == "EntityCollection"
    )


def test_the_default_order_is_newest_first_by_identity(store):
    page = store.search(Things, Criteria(pagination=Pagination(limit=3)))

    assert page.ids == ["th_0024", "th_0023", "th_0022"]


def test_walking_every_page_yields_each_record_exactly_once(store):
    seen = walk(store, Criteria(pagination=Pagination(limit=4)))

    assert len(seen) == ROW_COUNT
    assert len(set(seen)) == ROW_COUNT


def test_the_walk_survives_a_non_unique_ordering(store):
    """`size` repeats five times over, so the keyset has to lean on its tiebreaker."""
    seen = walk(store, Criteria(pagination=Pagination(limit=4), order=parse_order("size")))

    assert len(seen) == ROW_COUNT
    assert len(set(seen)) == ROW_COUNT


def test_the_walk_survives_a_mixed_ordering(store):
    """Mixed directions have no single tuple comparison; the compiler expands them."""
    seen = walk(
        store, Criteria(pagination=Pagination(limit=4), order=parse_order("size,-made_at"))
    )

    assert len(seen) == ROW_COUNT
    assert len(set(seen)) == ROW_COUNT


def test_a_row_inserted_mid_walk_never_repeats_or_hides_another(store):
    """The case `OFFSET` gets wrong: an insert shifts every position after it."""
    criteria = Criteria(pagination=Pagination(limit=4))
    first = store.search(Things, criteria)
    store.save(a_thing(900))

    rest = walk(store, criteria.resuming_from(first.cursor))
    seen = first.ids + rest

    assert len(seen) == len(set(seen))
    assert set(seen) >= {f"th_{n:04d}" for n in range(ROW_COUNT)}


def test_a_short_first_page_is_counted_for_free(store, queries_run):
    """Everything that matched came back, so the total is known without asking again."""
    with queries_run() as statements:
        page = store.search(Things, Criteria(pagination=Pagination(limit=ROW_COUNT + 10)))

    assert page.count is not None and page.count.exact
    assert page.count.value == ROW_COUNT
    assert len(statements) == 1


def test_a_full_page_pays_exactly_one_extra_statement_for_its_count(store, queries_run):
    with queries_run() as statements:
        page = store.search(Things, Criteria(pagination=Pagination(limit=5)))

    assert page.count is not None and page.count.value == ROW_COUNT
    assert len(statements) == 2


def test_asking_for_no_count_asks_the_database_once(store, queries_run):
    with queries_run() as statements:
        page = store.search(
            Things, Criteria(pagination=Pagination(limit=5), count=CountMode.NONE)
        )

    assert page.count is None
    assert len(statements) == 1


def test_the_count_stops_at_the_ceiling_and_says_so(store, monkeypatch):
    """A ceiling of two over twenty-five rows: the answer is a floor, and it knows it."""
    monkeypatch.setattr(
        "sincpro_framework.orm.sqlalchemy.repository.DEFAULT_COUNT_CAP",
        2,
    )
    page = store.search(Things, Criteria(pagination=Pagination(limit=1)))

    assert page.count is not None
    assert (page.count.value, page.count.exact) == (2, False)
    assert str(page.count) == "2+"


def test_a_page_knows_it_is_a_fragment_and_refuses_to_fold_itself(store):
    page = store.search(Things, Criteria(pagination=Pagination(limit=5)))

    assert page.is_partial
    with pytest.raises(Exception, match="self.repository.totals"):
        page.sum_by(lambda thing: thing.size)


def test_ordering_by_a_nullable_column_is_refused(store):
    """The one refusal in the compiler: a NULL in a keyset column drops the row silently."""
    with pytest.raises(InvalidCriteria, match="nullable"):
        store.search(Things, Criteria(order=parse_order("owner")))


def test_ordering_by_a_field_that_does_not_exist_is_refused(store):
    with pytest.raises(InvalidCriteria, match="no such field"):
        store.search(Things, Criteria(order=parse_order("nope")))


def test_an_unknown_filter_is_dropped_reported_and_does_not_narrow(store):
    page = store.search(
        Things,
        Criteria(
            where=Condition(field="legacy", value="1"),
            pagination=Pagination(limit=ROW_COUNT + 10),
        ),
    )

    assert len(page) == ROW_COUNT
    assert [(one.field, one.reason) for one in page.dropped] == [("legacy", "unknown_field")]


def test_contains_finds_a_member_of_a_list_column(store):
    page = store.search(
        Things,
        Criteria(
            where=Condition(field="tags", operator=Operator.CONTAINS, value="even"),
            pagination=Pagination(limit=ROW_COUNT + 10),
        ),
    )

    assert len(page) == len([n for n in range(ROW_COUNT) if n % 2 == 0])


def test_like_is_case_insensitive_containment(store):
    page = store.search(
        Things,
        Criteria(
            where=Condition(field="name", operator=Operator.LIKE, value="THING 1"),
            pagination=Pagination(limit=ROW_COUNT + 10),
        ),
    )

    assert page.ids and all("thing 1" in thing.name for thing in page)


def test_browsing_returns_the_records_in_the_order_the_ids_were_given(store):
    found = store.browse(Things, ["th_0005", "th_0001", "missing"])

    assert found.ids == ["th_0005", "th_0001"]
    assert store.browse(Things, []).ids == []


def test_grouping_answers_about_the_whole_set_in_one_statement(store, queries_run):
    with queries_run() as statements:
        buckets = store.group_by(Things, ["size"])

    assert {bucket["size"]: bucket["count"] for bucket in buckets} == {
        0: 5,
        1: 5,
        2: 5,
        3: 5,
        4: 5,
    }
    assert len(statements) == 1


def test_grouping_by_a_field_that_does_not_exist_is_refused(store):
    with pytest.raises(InvalidCriteria, match="no such field"):
        store.group_by(Things, ["nope"])


def test_aggregating_folds_the_whole_set_under_the_caller_s_names(store):
    folded = store.totals(Things, None, total="sum:size", biggest="max:size")

    assert folded == {"total": sum(n % 5 for n in range(ROW_COUNT)), "biggest": 4}


def test_a_malformed_fold_says_how_it_should_read(store):
    with pytest.raises(InvalidCriteria, match="function:field"):
        store.totals(Things, None, total="size")


def test_counting_on_its_own_never_reports_an_empty_result_as_a_total(store):
    """Context: the free-count shortcut applies to a page, not to a bare count. Reusing it
    here would answer `0` for every count, because no page was fetched."""
    assert store.count(Things, Criteria(pagination=Pagination(limit=5))).value == ROW_COUNT
    assert store.count(Things, Criteria(count=CountMode.EXACT)).value == ROW_COUNT


def test_the_escape_hatch_returns_a_real_statement_and_comes_back_to_the_envelope(store):
    criteria = Criteria(pagination=Pagination(limit=3))
    statement = store.statement(Things, criteria)

    narrowed = statement.where(Thing.size == 0)
    page = store.run(Things, narrowed, criteria)

    assert all(thing.size == 0 for thing in page)
    assert type(page) is Things


def test_counting_rows_is_the_other_page_strategy(store):
    """What everybody does, and here one more strategy: the page is located by counting rows
    instead of naming one. It costs what it costs — the database walks the skipped rows — and
    that is why `Cursor` is the default."""
    first = store.search(Things, Criteria(pagination=Pagination(limit=3)))
    third = store.search(
        Things, Criteria(pagination=Pagination(limit=3, strategy=Offset(rows=6)))
    )

    assert len(third.items) == 3
    assert not set(third.ids) & set(first.ids)


def test_counting_rows_mints_no_token_to_continue(store):
    """Whoever counts rows already knows the next count; a cursor there would mean nothing."""
    page = store.search(
        Things, Criteria(pagination=Pagination(limit=3, strategy=Offset(rows=0)))
    )

    assert page.cursor is None


def test_the_concrete_repository_honours_the_protocol(store):
    """The use case takes the concrete instance; the protocol holds it to the minimum."""
    assert isinstance(store, RepositoryProtocol)
