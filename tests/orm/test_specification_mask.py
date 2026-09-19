"""The mask against a real model: what the definition keeps and drops, and that a paged answer
shows the fields that were named, plus the identity, and nothing else, while the records inside
the process stay whole.
"""

import pytest

from sincpro_framework.ddd.criteria import Criteria, Specification
from sincpro_framework.ddd.criteria.pagination import Pagination
from sincpro_framework.ddd.query import ResponsePaginatedQuery
from sincpro_framework.orm.sqlalchemy.model_introspection import describe

from .models import Thing, Things

META = describe(Thing)


class ResponseThings(ResponsePaginatedQuery):
    things: list[Thing]


def test_the_model_keeps_what_it_has_and_names_what_it_does_not():
    kept, dropped = META.accept_specification(
        Specification({"name": Criteria(), "legacy_flag": Criteria()})
    )

    assert kept is not None and kept.named == ["name"]
    assert [(one.field, one.reason) for one in dropped] == [("legacy_flag", "unknown_field")]


def test_a_mask_nobody_wrote_is_left_alone():
    assert META.accept_specification(None) == (None, [])


def test_a_mask_the_model_empties_still_counts_as_asked():
    kept, dropped = META.accept_specification(Specification({"legacy_flag": Criteria()}))

    assert kept is not None and kept.named == []
    assert len(dropped) == 1


@pytest.mark.parametrize(
    "asked, expected",
    [
        (["name"], ["thing_id", "name"]),
        (["size", "name"], ["thing_id", "size", "name"]),
        (["thing_id"], ["thing_id"]),
    ],
)
def test_the_definition_comes_back_cut_the_same_way(asked, expected):
    """A mask governs the payload AND what the interface may offer, so a filter builder
    cannot show a field that was not delivered."""
    mask = Specification({name: Criteria() for name in asked})

    assert list(META.only(mask).fields) == expected


def test_the_identity_survives_a_mask_that_did_not_name_it():
    """A record whose id did not travel cannot be opened, refreshed or cached."""
    assert list(META.only(Specification({})).fields) == ["thing_id"]


def test_without_a_mask_the_definition_is_whole_but_expands_nothing():
    whole = META.only(None)

    assert len(whole.fields) == len(META.fields)
    assert whole.relations == {}


def test_a_paged_answer_shows_the_named_fields_and_the_identity(store):
    asked = Criteria.model_validate(
        {"specification": {"name": {}, "legacy_flag": {}}, "pagination": {"limit": 3}}
    )

    page = store.search(Things, asked)
    answer = ResponseThings.of(page, asked)
    on_the_wire = answer.model_dump()

    assert all(set(record) == {"thing_id", "name"} for record in on_the_wire["things"])
    assert list(on_the_wire["model_meta_data"]["fields"]) == ["thing_id", "name"]
    assert on_the_wire["dropped"] == [{"field": "legacy_flag", "reason": "unknown_field"}]
    assert answer.things[0].size is not None  # inside the process the record is whole


def test_without_a_specification_the_answer_is_whole(store):
    asked = Criteria(pagination=Pagination(limit=2))

    on_the_wire = ResponseThings.of(store.search(Things, asked), asked).model_dump()

    assert set(on_the_wire["things"][0]) == {
        "thing_id",
        "name",
        "size",
        "tags",
        "made_at",
        "owner",
    }
    assert set(on_the_wire["model_meta_data"]["fields"]) == set(META.fields)
