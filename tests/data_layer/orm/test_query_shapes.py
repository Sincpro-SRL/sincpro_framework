from sincpro_framework import exceptions
from sincpro_framework.ddd import Pagination

"""The two shapes every read inherits, and the convention that makes the second one work.

`ResponsePaginatedQuery.of` fills the subclass's own field with the records, which only works
while a subclass adds exactly one. That is a convention, so it is worth a test: the day someone
adds a second field, they should be told rather than have their records land in whichever one
happened to be declared first.
"""

import pytest

from sincpro_framework.ddd.criteria import Criteria
from sincpro_framework.ddd.entity.entity_collection import Count, Dropped, EntityCollection
from sincpro_framework.ddd.metamodel import model_of
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


def test_the_answer_carries_the_version_of_its_model_never_the_model(store):
    """What may be filtered, ordered and shown is published once, in the discovery document; a
    page answers the version of the model it read, which a client compares with the one it
    loaded."""
    page = store.search(Things, Criteria(pagination=Pagination(limit=1)))

    written = ResponseListThings.of(page, Criteria()).model_dump(mode="json")

    assert written["model_version"] == model_of(Thing).fingerprint
    assert "entity_meta_data" not in written


def test_a_page_built_by_hand_answers_the_version_too():
    """The version is the model's, not the page's: a page nobody read from a store still says
    which model its records are."""
    page = EntityCollection(items=(a_thing(1),))

    assert (
        ResponseListThings.of(page, Criteria()).model_version == model_of(Thing).fingerprint
    )


def test_a_response_that_declares_two_fields_is_refused():
    class Ambiguous(ResponsePaginatedQuery):
        things: list[Thing]
        extras: list[Thing]

    with pytest.raises(exceptions.ProgrammingError, match="declares 2 fields of its own"):
        Ambiguous.records_field()


def test_a_response_that_declares_none_is_refused_too():
    class Empty(ResponsePaginatedQuery):
        pass

    with pytest.raises(exceptions.ProgrammingError, match="declares 0 fields"):
        Empty.records_field()


class TestWhatTheOpenApiPublishes:
    """A paginated answer has to describe itself, or every client that generates types writes
    them by hand and drifts from what the engine actually sends."""

    def test_the_answer_is_published_with_every_part_it_carries(self):
        """**In serialization mode, which is the one a response uses.** A model with a plain
        serializer tells pydantic only "I write an object"; without the schema hook this comes
        back as `{"type": "object"}` and a generated client knows nothing.
        """

        class ResponseListThings(ResponsePaginatedQuery):
            things: list[Thing] = []

        published = ResponseListThings.model_json_schema(mode="serialization")

        assert sorted(published["properties"]) == [
            "count",
            "cursor",
            "dropped",
            "model_version",
            "things",
        ]
        assert published["properties"]["things"]["items"]["$ref"].endswith("Thing")
        assert "Count" in str(published["properties"]["count"])
        assert published["properties"]["model_version"]["type"] == "string"

    def test_it_says_the_same_thing_in_both_modes(self):
        """The serializer writes the declared fields and no others, so the two schemas name the
        same keys. A difference between them would mean a client validating one and receiving
        the other."""

        class ResponseListThings(ResponsePaginatedQuery):
            things: list[Thing] = []

        validation = ResponseListThings.model_json_schema(mode="validation")
        serialization = ResponseListThings.model_json_schema(mode="serialization")

        assert sorted(validation["properties"]) == sorted(serialization["properties"])

    def test_it_warns_that_a_specification_cuts_the_records(self):
        """The one thing the schema cannot express: a mask removes fields from every record.
        Saying it in the description is what keeps a generated type from promising a field that
        will not always arrive."""

        class ResponseListThings(ResponsePaginatedQuery):
            things: list[Thing] = []

        published = ResponseListThings.model_json_schema(mode="serialization")

        assert "specification" in published["description"]

    def test_what_it_publishes_is_what_it_writes(self):
        """The claim under the schema: these are the keys a real answer carries."""

        class ResponseListThings(ResponsePaginatedQuery):
            things: list[Thing] = []

        answer = ResponseListThings.of(
            EntityCollection(items=(a_thing(1),), count=Count(value=1, exact=True)),
            Criteria(),
        )
        written = answer.model_dump(mode="json")
        published = ResponseListThings.model_json_schema(mode="serialization")

        assert sorted(written) == sorted(published["properties"])
