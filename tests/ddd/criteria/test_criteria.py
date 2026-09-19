"""What a caller sends: the filter, the ordering, the page and the grouping.

**One form for each thing.** No abbreviated spelling, no positional form, no mapping the engine
has to guess: what the type declares is what is accepted, and what travels as JSON is that same
object. These tests are the specification — what is not here is not supported.
"""

from datetime import datetime
from decimal import Decimal

import pytest

from sincpro_framework.ddd.criteria import Measure
from sincpro_framework.ddd.exceptions import InvalidCriteria as _InvalidCriteria


def test_a_fold_only_takes_a_known_aggregate():
    """The function name reaches SQL: anything outside the closed list is refused here."""
    assert Measure(function="sum", field="row_count").function == "sum"
    with pytest.raises(_InvalidCriteria, match="not a measure"):
        Measure(function="pg_sleep", field="row_count")


from sincpro_framework.ddd import Criteria, Cursor, Pagination
from sincpro_framework.ddd.criteria import (
    All,
    Any_,
    Condition,
    Grouping,
    Level,
    Measure,
    Not,
    Operator,
    Sort,
    combined,
    conditions_of,
)
from sincpro_framework.ddd.criteria.pagination import DEFAULT_LIMIT, Offset
from sincpro_framework.ddd.exceptions import InvalidCriteria

# ── the filter ─────────────────────────────────────────────────────────────────

OVER_A_THOUSAND = Condition(field="row_count", operator=Operator.GT, value=1000)
NAMED_LABS = Condition(field="name", operator=Operator.LIKE, value="labs%")


def test_a_condition_is_field_operator_and_value():
    """The one form. No `{"row_count__gt": 1000}` and no `["row_count", ">", 1000]`."""
    asked = Criteria(where=OVER_A_THOUSAND)

    assert asked.expression == OVER_A_THOUSAND


def test_the_operator_is_written_as_the_definition_publishes_it():
    """One vocabulary: the enum's value IS the symbol, so the definition's `ops` and what a
    condition accepts are the same thing. The definition used to say `gt` while the filter
    took `>`."""
    assert Operator.GT == ">"
    assert Operator.IS_NULL == "is null"
    assert Condition(field="a", operator=Operator.GT, value=1).operator is Operator.GT


def test_a_group_is_an_object_with_all_any_or_negate():
    conjunction = Criteria.model_validate(
        {"where": {"all": [OVER_A_THOUSAND, NAMED_LABS]}}
    ).expression
    disjunction = Criteria.model_validate(
        {"where": {"any": [OVER_A_THOUSAND, NAMED_LABS]}}
    ).expression
    negation = Criteria.model_validate({"where": {"negate": OVER_A_THOUSAND}}).expression

    assert conjunction == All(all=[OVER_A_THOUSAND, NAMED_LABS])
    assert disjunction == Any_(any=[OVER_A_THOUSAND, NAMED_LABS])
    assert negation == Not(negate=OVER_A_THOUSAND)


def test_groups_nest_without_limit():
    """Context: the first version looked only at the top level, so the branches of an `any`
    stayed raw and pydantic dropped the whole filter — a listing that asked for two origins
    showed all of them."""
    tree = Criteria.model_validate(
        {
            "where": {
                "all": [
                    OVER_A_THOUSAND,
                    {"any": [NAMED_LABS, {"negate": OVER_A_THOUSAND}]},
                ]
            }
        }
    ).expression

    assert isinstance(tree, All)
    assert isinstance(tree.all[1], Any_)
    assert isinstance(tree.all[1].any[1], Not)


def test_a_filter_that_is_neither_form_is_refused():
    """An API that accepts anything and guesses is one that fails in silence."""
    with pytest.raises(InvalidCriteria, match="'all', 'any' or 'negate'"):
        Criteria.model_validate({"where": {"row_count__gt": 1000}})


def test_the_filter_travels_in_a_url_as_its_own_json():
    """Not another form: the same one inside a parameter."""
    from_url = Criteria.model_validate(
        {"where": '{"field": "row_count", "operator": ">", "value": 1000}'}
    )

    assert from_url.expression == OVER_A_THOUSAND


def test_unreadable_json_is_refused_naming_the_problem():
    with pytest.raises(InvalidCriteria, match="does not read as JSON"):
        Criteria.model_validate({"where": "this is not json"})


# ── the page ───────────────────────────────────────────────────────────────────


def test_a_page_has_how_many_and_from_where_as_separate_things():
    """How many belongs to the page; from where is a STRATEGY. The cursor is one of them, not
    the object."""
    first = Pagination(limit=80)
    following = Pagination(limit=80, strategy=Cursor(token="eyJ"))
    counting = Pagination(limit=80, strategy=Offset(rows=160))

    assert first.strategy == Cursor(token=None)
    assert following.cursor == "eyJ"
    assert counting.cursor is None and counting.strategy.skipped() == 160


def test_the_page_has_a_default_and_no_ceiling():
    """A framework says what happens when nobody chooses; it does not decide for the
    application how much it may ask for. There used to be a cap of 500 answering 422."""
    assert Criteria().limit == DEFAULT_LIMIT
    assert Criteria(pagination=Pagination(limit=5000)).limit == 5000


def test_resuming_a_reading_keeps_everything_but_where_it_starts():
    """`model_copy(update={"cursor": …})` does nothing — `cursor` is a read-through property —
    and a loop that paged that way never ended."""
    asked = Criteria(where=OVER_A_THOUSAND, pagination=Pagination(limit=2))

    resumed = asked.resuming_from("eyJ")

    assert resumed.cursor == "eyJ"
    assert resumed.limit == 2
    assert resumed.expression == asked.expression


# ── the grouping ───────────────────────────────────────────────────────────────


def test_the_grouping_declares_its_levels_and_its_folds():
    asked = Criteria.model_validate(
        {
            "grouping": {
                "group_by": [
                    {"field": "produced_by"},
                    {"field": "registered_at", "grain": "month"},
                ],
                "measures": {"rows": ["sum", "row_count"]},
            }
        }
    )

    assert asked.grouping.group_by == (
        Level(field="produced_by"),
        Level(field="registered_at", grain="month"),
    )
    assert asked.grouping.measures == {"rows": Measure(function="sum", field="row_count")}


def test_a_misspelt_level_or_fold_is_refused():
    with pytest.raises(InvalidCriteria, match="a level is written"):
        Criteria.model_validate({"grouping": {"group_by": ["produced_by"]}})
    with pytest.raises(InvalidCriteria, match="a measure is written"):
        Criteria.model_validate(
            {"grouping": {"group_by": [{"field": "a"}], "measures": {"n": "sum:row_count"}}}
        )


# ── merging two readings ───────────────────────────────────────────────────────


def test_filters_accumulate_with_and():
    merged = Criteria(where=OVER_A_THOUSAND).merged_with(Criteria(where=NAMED_LABS))

    assert merged.expression == All(all=[OVER_A_THOUSAND, NAMED_LABS])


def test_merging_does_not_nest_what_is_one_where():
    """Criteria get merged repeatedly — a saved reading, what the user typed, what the
    interface always adds — and wrapping each merge would nest as deep as the merges."""
    flat = combined(All(all=[OVER_A_THOUSAND]), NAMED_LABS)

    assert flat == All(all=[OVER_A_THOUSAND, NAMED_LABS])


def test_the_page_is_scalar_and_the_explicit_one_wins():
    """ "Twenty per page" and "fifty per page" have no combination."""
    asked = Criteria(pagination=Pagination(limit=20))

    assert asked.merged_with(Criteria(pagination=Pagination(limit=5))).limit == 5
    assert asked.merged_with(Criteria()).limit == 20


def test_the_cursor_never_survives_a_merge_that_did_not_bring_it():
    """It belongs to one reading, and this is another."""
    holding = Criteria(pagination=Pagination(strategy=Cursor(token="eyJ")))

    assert holding.merged_with(Criteria()).cursor is None


def test_turning_the_definition_off_survives_a_merge():
    """With the definition travelling by default, the opposite rule made `meta=False`
    impossible to say."""
    quiet = Criteria(meta=False)

    assert quiet.merged_with(Criteria()).meta is False
    assert Criteria().merged_with(quiet).meta is False
    assert Criteria().merged_with(Criteria()).meta is True


# ── reading the tree ───────────────────────────────────────────────────────────


def test_every_leaf_is_reachable_whatever_the_shape():
    tree = All(all=[Any_(any=[OVER_A_THOUSAND, NAMED_LABS]), Not(negate=OVER_A_THOUSAND)])

    assert conditions_of(tree) == [OVER_A_THOUSAND, NAMED_LABS, OVER_A_THOUSAND]
    assert conditions_of(None) == []


def test_a_criteria_survives_its_own_json():
    """That is how one travels to a queue, to another service or in the body of a POST."""
    asked = Criteria(
        where=All(all=[OVER_A_THOUSAND, NAMED_LABS]),
        order=(Sort(field="registered_at", descending=True),),
        pagination=Pagination(limit=80, strategy=Cursor(token="eyJ")),
        grouping=Grouping(group_by=(Level(field="produced_by"),)),
    )

    assert Criteria.model_validate_json(asked.model_dump_json()) == asked


def test_a_condition_built_from_python_takes_dates_and_decimals():
    """A Feature need not serialise a date to text for the model to read it back; in the JSON
    it comes out as text all the same, and a text stays a text."""
    moment = datetime(2026, 3, 14, 15, 9, 26)
    with_date = Condition(field="made_at", operator=Operator.GT, value=moment)
    with_amount = Condition(field="total", operator=Operator.GTE, value=Decimal("10.50"))
    with_text = Condition(field="made_at", operator=Operator.GT, value="2026-03-14")

    assert with_date.value == moment
    assert isinstance(with_amount.value, Decimal)
    assert with_text.value == "2026-03-14"
    assert Criteria(where=with_date).model_dump(mode="json")["where"]["value"] == (
        "2026-03-14T15:09:26"
    )
    assert Condition(field="n", value=5).value == 5
    assert type(Condition(field="n", value=5).value) is int
