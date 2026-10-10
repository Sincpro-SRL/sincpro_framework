"""`QueryCache`: what was read is never read again — the next page is the only read, a narrower
filter over a complete result is none, and another tenant's rows are never handed over."""

import pytest

from sincpro_framework import ProgrammingError
from sincpro_framework.data_layer.data_analysis import DataFrame, QueryCache
from sincpro_framework.ddd.criteria import Offset, Pagination
from tests.data_layer.data_analysis.conftest import POSTED, CountingRepository, Line, criteria


def test_asking_again_for_what_is_held_reads_nothing(repository: CountingRepository):
    cache = QueryCache()

    first = cache.fetch(repository, Line, criteria(limit=100))
    again = cache.fetch(repository, Line, criteria(limit=100))

    assert repository.reads == 1
    assert len(first) == 100 and again is first


def test_more_pages_of_the_same_query_read_only_the_pages_missing(
    repository: CountingRepository,
):
    cache = QueryCache()
    cache.fetch(repository, Line, criteria(limit=100))

    three = cache.fetch(repository, Line, criteria(limit=100), pages=3)

    assert repository.reads == 3
    assert len(three) == 300 and len(set(three.column("id"))) == 300
    assert three.column("id") == sorted(three.column("id"))


def test_a_complete_result_answers_a_narrower_filter_with_no_read(
    repository: CountingRepository,
):
    cache = QueryCache()
    everything = cache.fetch_all(repository, Line, criteria(limit=250))
    reads = repository.reads

    sales = everything.narrow({"field": "journal", "operator": "=", "value": "SAL"})

    assert repository.reads == reads
    assert everything.complete and sales.complete
    assert set(sales.column("journal")) == {"SAL"} and set(sales.column("state")) == {
        "posted"
    }
    assert len(sales) == sum(
        1 for journal in everything.column("journal") if journal == "SAL"
    )


def test_a_result_that_is_not_complete_cannot_answer_a_narrower_filter(
    repository: CountingRepository,
):
    partial = QueryCache().fetch(repository, Line, criteria(limit=100))

    with pytest.raises(ProgrammingError, match="100 rows.*fetch_all"):
        partial.narrow({"field": "journal", "operator": "=", "value": "SAL"})


def test_a_different_filter_is_another_query(repository: CountingRepository):
    cache = QueryCache()
    cache.fetch(repository, Line, criteria())

    cache.fetch(
        repository,
        Line,
        criteria(where={"field": "state", "operator": "=", "value": "draft"}),
    )

    assert repository.reads == 2 and len(cache) == 2


def test_invalidate_drops_what_is_held_of_an_aggregate(repository: CountingRepository):
    cache = QueryCache()
    cache.fetch(repository, Line, criteria())

    cache.invalidate(Line)
    cache.fetch(repository, Line, criteria())

    assert repository.reads == 2


def test_the_oldest_result_is_let_go_past_the_rows_allowed(repository: CountingRepository):
    cache = QueryCache(max_rows=250)
    older = cache.fetch(repository, Line, criteria(limit=200))
    cache.fetch(
        repository,
        Line,
        criteria(limit=200, where={"field": "state", "operator": "=", "value": "draft"}),
    )

    assert cache.get(repository, Line, criteria(limit=200)) is None
    assert len(older) == 200


def test_a_frame_says_what_it_answers(repository: CountingRepository):
    frame = QueryCache().fetch(repository, Line, criteria(limit=10))

    assert isinstance(frame, DataFrame)
    assert frame.where == {"field": "state", "operator": "=", "value": "posted"} == POSTED
    assert {"id", "journal", "amount"} <= set(frame.columns)
    assert not frame.complete and len(frame.version) == 16


def test_a_read_starts_from_the_first_row_whatever_page_it_was_asked_from(
    repository: CountingRepository,
):
    cache = QueryCache()
    from_the_middle = criteria(limit=100).model_copy(
        update={"pagination": Pagination(limit=100, strategy=Offset(rows=500))}
    )

    held = cache.fetch(repository, Line, from_the_middle)
    first = cache.fetch(repository, Line, criteria(limit=100))

    assert first is held
    assert first.column("id")[0] == "l00001"


def test_a_read_asked_for_is_the_one_used_last(repository: CountingRepository):
    cache = QueryCache(max_rows=250)
    sal = {"field": "journal", "operator": "=", "value": "SAL"}
    pur = {"field": "journal", "operator": "=", "value": "PUR"}
    bnk = {"field": "journal", "operator": "=", "value": "BNK"}
    cache.fetch(repository, Line, criteria(limit=100, where=sal))
    cache.fetch(repository, Line, criteria(limit=100, where=pur))

    cache.get(repository, Line, criteria(limit=100, where=sal))
    cache.fetch(repository, Line, criteria(limit=100, where=bnk))

    assert cache.get(repository, Line, criteria(limit=100, where=sal)) is not None
    assert cache.get(repository, Line, criteria(limit=100, where=pur)) is None
