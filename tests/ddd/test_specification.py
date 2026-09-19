"""What is wanted OF each record: the mapping, and how two masks cross.

The test worth reading twice is the one where a mask loses all of its fields. «Nothing was
asked» and «something was asked and nothing survived» are opposite answers, and reading them
alike makes a mask hand back the whole record, which is exactly what it existed to prevent.
"""

from sincpro_framework.ddd.criteria import (
    All,
    Condition,
    Criteria,
    Grouping,
    Level,
    Operator,
    Sort,
    Specification,
)
from sincpro_framework.ddd.pagination import Cursor, Pagination

BIGGER_THAN_A_THOUSAND = Condition(field="row_count", value=1000, operator=Operator.GT)


def test_every_value_is_a_criteria_so_there_is_nothing_to_tell_apart():
    """One type and no union: a consumer reads `dict[str, Criteria]` in any language."""
    asked = Specification({"name": Criteria(), "derived_from": Criteria()})

    assert asked.named == ["name", "derived_from"]
    assert isinstance(asked["name"], Criteria)


def test_a_relation_carries_the_same_vocabulary_as_the_level_above():
    asked = Criteria.model_validate(
        {
            "specification": {
                "name": {},
                "sources": {
                    "specification": {"row_count": {}},
                    "order": [{"field": "row_count", "descending": True}],
                    "pagination": {"limit": 20},
                },
            }
        }
    )

    assert asked.specification is not None
    sources = asked.specification["sources"]
    assert sources.limit == 20
    assert sources.specification is not None
    assert sources.specification.named == ["row_count"]
    assert sources.order == (Sort(field="row_count", descending=True),)


def test_the_mask_travels_as_the_bare_mapping():
    """No extra key that means nothing: in JSON it is what was written."""
    asked = Criteria.model_validate({"specification": {"name": {}}})

    assert asked.model_dump(exclude_defaults=True)["specification"] == {"name": {}}


def test_asking_nothing_is_not_asking_and_being_left_with_nothing():
    assert Criteria().specification is None
    assert Specification({}).named == []


def test_two_masks_cross_on_what_both_allow():
    mine = Specification({"name": Criteria(), "row_count": Criteria()})

    assert mine.narrowed_by(Specification({"name": Criteria()})).named == ["name"]


def test_a_mask_over_everything_is_the_mask():
    assert Specification({"name": Criteria()}).narrowed_by(None).named == ["name"]


def test_two_masks_with_nothing_in_common_leave_nothing_and_not_everything():
    """All the safety of this: `{}` reaches the engine as «the identity alone»."""
    empty = Specification({"name": Criteria()}).narrowed_by(
        Specification({"row_count": Criteria()})
    )

    assert empty.named == []


def test_merging_two_criterias_intersects_the_mask_instead_of_adding():
    """A filter accumulates because both restrictions hold; a mask can only take away."""
    mine = Criteria.model_validate({"specification": {"name": {}, "row_count": {}}})
    theirs = Criteria.model_validate({"specification": {"name": {}}})

    together = mine.merged_with(theirs).specification

    assert together is not None and together.named == ["name"]
    assert Criteria().merged_with(theirs).specification == theirs.specification
    assert mine.merged_with(Criteria()).specification == mine.specification


def test_a_nested_criteria_merges_inside_its_node():
    """Crossing two masks also crosses what each says about a shared relation."""
    mine = Specification({"sources": Criteria(pagination=Pagination(limit=20))})
    theirs = Specification({"sources": Criteria(where=BIGGER_THAN_A_THOUSAND)})

    crossed = mine.narrowed_by(theirs)["sources"]

    assert crossed.limit == 20
    assert crossed.expression == BIGGER_THAN_A_THOUSAND


def test_the_mask_is_walked_as_what_it_is():
    """A mapping: iterated, measured and asked for a name, so nobody reaches for `.root`."""
    mask = Specification({"name": Criteria(), "row_count": Criteria()})

    assert list(mask) == ["name", "row_count"]
    assert len(mask) == 2
    assert "name" in mask and "legacy_flag" not in mask


def test_a_criteria_with_a_nested_specification_survives_its_own_json():
    asked = Criteria(
        where=All(all=[BIGGER_THAN_A_THOUSAND]),
        order=(Sort(field="registered_at", descending=True),),
        pagination=Pagination(limit=80, strategy=Cursor(token="eyJ")),
        specification=Specification(
            {
                "name": Criteria(),
                "sources": Criteria(specification=Specification({"row_count": Criteria()})),
            }
        ),
        grouping=Grouping(group_by=(Level(field="produced_by"),)),
    )

    assert Criteria.model_validate_json(asked.model_dump_json()) == asked
