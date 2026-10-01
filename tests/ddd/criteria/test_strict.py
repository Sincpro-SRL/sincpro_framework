"""A query is read strictly: a key the language does not have is refused, never ignored — because
a dropped key does not fail, it answers wrong. A reading may ask to tolerate them."""

import pytest
from pydantic import ValidationError

from sincpro_framework.ddd.criteria import TOLERANT, Criteria
from sincpro_framework.ddd.exceptions import InvalidCriteria


def test_a_key_the_criteria_does_not_have_is_refused():
    with pytest.raises(ValidationError, match="limit"):
        Criteria.model_validate({"limit": 10})


def test_a_key_a_page_does_not_have_is_refused():
    with pytest.raises(ValidationError, match="size"):
        Criteria.model_validate({"pagination": {"size": 10}})


def test_a_key_an_ordering_does_not_have_is_refused():
    with pytest.raises(ValidationError, match="direction"):
        Criteria.model_validate({"order": [{"field": "total", "direction": "desc"}]})


def test_a_key_a_condition_does_not_have_is_refused():
    with pytest.raises(InvalidCriteria, match="op"):
        Criteria.model_validate({"where": {"field": "total", "op": ">", "value": 1}})


def test_a_group_that_mixes_all_and_any_is_refused_not_half_read():
    with pytest.raises(InvalidCriteria, match="all"):
        Criteria.model_validate({"where": {"any": [], "all": []}})


def test_a_tolerant_reading_leaves_out_what_it_does_not_know_at_every_depth():
    criteria = Criteria.model_validate(
        {
            "limit": 10,
            "pagination": {"limit": 20, "size": 5},
            "order": [{"field": "total", "direction": "desc"}],
            "where": {
                "all": [{"field": "total", "operator": ">", "value": 1, "op": "?"}],
                "x": 1,
            },
        },
        context=TOLERANT,
    )

    assert criteria.pagination.limit == 20
    assert [sort.field for sort in criteria.order] == ["total"]
    assert criteria.expression is not None


def test_what_the_language_has_reads_as_it_always_did():
    criteria = Criteria.model_validate(
        {
            "where": {"field": "total", "operator": ">", "value": 1},
            "order": [{"field": "total", "descending": True}],
            "pagination": {"limit": 10},
            "specification": {"lines": {"pagination": {"limit": 5}}},
        }
    )

    assert criteria.pagination.limit == 10 and criteria.order[0].descending


def test_a_tolerant_reading_reaches_inside_the_grouping_too():
    criteria = Criteria.model_validate(
        {
            "grouping": {
                "group_by": [{"field": "state", "colour": "red"}],
                "measures": {"total": {"function": "sum", "field": "total", "x": 1}},
                "where_measures": {"field": "total", "operator": ">", "value": 1, "op": "?"},
            }
        },
        context=TOLERANT,
    )

    assert [level.field for level in criteria.grouping.group_by] == ["state"]


def test_a_strict_reading_refuses_an_unknown_key_inside_the_grouping():
    with pytest.raises(InvalidCriteria, match="colour"):
        Criteria.model_validate(
            {"grouping": {"group_by": [{"field": "state", "colour": "red"}]}}
        )
