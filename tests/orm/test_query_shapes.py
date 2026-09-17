from sincpro_framework.ddd import Pagination

"""The two shapes every read inherits, and the convention that makes the second one work.

`ResponsePaginatedQuery.of` fills the subclass's own field with the records, which only works
while a subclass adds exactly one. That is a convention, so it is worth a test: the day someone
adds a second field, they should be told rather than have their records land in whichever one
happened to be declared first.
"""

import pytest

from sincpro_framework.ddd.criteria import Criteria
from sincpro_framework.ddd.entity_collection import Count, Dropped, EntityCollection
from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.ddd.query import Query, ResponsePaginatedQuery

from .models import Thing, Things, a_thing


class CommandListThings(Query):
    pass


class ResponseListThings(ResponsePaginatedQuery):
    things: list[Thing]


def test_a_query_command_carries_a_criteria_and_nothing_else():
    assert CommandListThings().criteria == Criteria()
    assert (
        CommandListThings(criteria=Criteria(pagination=Pagination(limit=3))).criteria.limit
        == 3
    )


def test_a_response_names_its_own_records():
    assert ResponseListThings.records_field() == "things"


def test_a_page_becomes_the_answer_that_goes_out():
    page = EntityCollection(
        items=(a_thing(1), a_thing(2)),
        cursor="eyJrIjpbXX0",
        count=Count(value=25, exact=True),
        dropped=(Dropped(field="gone", reason="unknown_field"),),
    )

    answer = ResponseListThings.of(page, Criteria())

    assert [one.thing_id for one in answer.things] == ["th_0001", "th_0002"]
    assert answer.cursor == "eyJrIjpbXX0"
    assert answer.count == Count(value=25, exact=True)
    assert answer.dropped == [Dropped(field="gone", reason="unknown_field")]


def test_the_model_definition_travels_unless_it_was_turned_off(store):
    """The page carries the definition the engine read to answer, and the answer describes
    itself — the use case never hands over the aggregate.

    It is on by default because the opposite made every door need a second question asked of
    it before it could be used. Off stays reachable for the caller that already knows the
    model and is paging through it.
    """
    page = store.search(Things, Criteria(pagination=Pagination(limit=1)))

    plain = ResponseListThings.of(page, Criteria())
    quiet = ResponseListThings.of(page, Criteria(meta=False))

    assert plain.model_meta_data is not None
    assert plain.model_meta_data.aggregate == "Thing"
    assert quiet.model_meta_data is None


def test_a_page_built_by_hand_carries_no_definition():
    """Nobody read a model, so there is nothing to describe — and nothing is invented."""
    page = EntityCollection(items=(a_thing(1),))

    assert ResponseListThings.of(page, Criteria()).model_meta_data is None


def test_a_response_that_declares_two_fields_is_refused():
    class Ambiguous(ResponsePaginatedQuery):
        things: list[Thing]
        extras: list[Thing]

    with pytest.raises(ContractViolation, match="declares 2 fields of its own"):
        Ambiguous.records_field()


def test_a_response_that_declares_none_is_refused_too():
    class Empty(ResponsePaginatedQuery):
        pass

    with pytest.raises(ContractViolation, match="declares 0 fields"):
        Empty.records_field()
