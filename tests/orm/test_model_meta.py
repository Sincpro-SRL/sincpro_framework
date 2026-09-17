"""Text from a URL becoming a filter this model can answer, and what gets left behind.

The tolerant half of the contract lives here, and the reason it is tolerant is narrow: a
shared link outlives the schema it was written against. Someone sends a filtered URL and it is
opened months later with a column renamed. Refusing kills the link; dropping in silence is
worse, because a dropped filter *widens* the result — so it is dropped and reported.

The pruning tests are the ones worth reading twice. A connective that loses all of its parts
has to disappear rather than become empty, because an empty `AND` matches everything and an
empty `OR` matches nothing — opposite answers to the same accident.
"""

from datetime import datetime

from sincpro_framework.ddd.criteria import All, Any_, Condition, Not, Operator
from sincpro_framework.orm.sqlalchemy.model_introspection import describe

from .models import Thing

META = describe(Thing)


def test_a_number_arrives_as_text_and_leaves_as_a_number():
    clean, dropped = META.accept(Condition(field="size", operator=Operator.GT, value="1000"))

    assert clean == Condition(field="size", operator=Operator.GT, value=1000)
    assert dropped == []


def test_a_datetime_arrives_as_text_and_leaves_as_a_datetime():
    clean, _ = META.accept(
        Condition(field="made_at", operator=Operator.LT, value="2026-03-14T15:09:26")
    )

    assert isinstance(clean, Condition)
    assert clean.value == datetime(2026, 3, 14, 15, 9, 26)


def test_is_null_reads_its_value_as_the_question_and_not_as_the_field_type():
    clean, _ = META.accept(Condition(field="owner", operator=Operator.IS_NULL, value="true"))

    assert isinstance(clean, Condition)
    assert clean.value is True


def test_every_member_of_an_in_is_coerced():
    clean, _ = META.accept(Condition(field="size", operator=Operator.IN, value=["1", "2"]))

    assert isinstance(clean, Condition)
    assert clean.value == [1, 2]


def test_contains_asks_about_a_member_not_about_a_list():
    clean, _ = META.accept(Condition(field="tags", operator=Operator.CONTAINS, value="even"))

    assert clean == Condition(field="tags", operator=Operator.CONTAINS, value="even")


def test_an_unknown_field_is_dropped_and_named():
    clean, dropped = META.accept(Condition(field="legacy_flag", value="1"))

    assert clean is None
    assert [(one.field, one.reason) for one in dropped] == [("legacy_flag", "unknown_field")]


def test_an_operator_the_type_does_not_take_is_dropped():
    clean, dropped = META.accept(Condition(field="size", operator=Operator.LIKE, value="x"))

    assert clean is None
    assert dropped[0].reason == "unsupported_operator"


def test_a_value_that_will_not_coerce_is_dropped():
    clean, dropped = META.accept(
        Condition(field="size", operator=Operator.GT, value="banana")
    )

    assert clean is None
    assert dropped[0].reason == "bad_value"


def test_the_usable_half_of_a_conjunction_survives():
    clean, dropped = META.accept(
        All(
            all=[
                Condition(field="name", operator=Operator.LIKE, value="labs"),
                Condition(field="legacy_flag", value="1"),
            ]
        ),
    )

    assert clean == Condition(field="name", operator=Operator.LIKE, value="labs")
    assert len(dropped) == 1


def test_a_connective_that_loses_everything_disappears():
    """An empty AND matches everything and an empty OR matches nothing; neither was asked."""
    clean, dropped = META.accept(
        Any_(
            any=[Condition(field="gone", value="1"), Condition(field="also_gone", value="1")]
        )
    )

    assert clean is None
    assert len(dropped) == 2


def test_a_negation_of_nothing_disappears_too():
    clean, _ = META.accept(Not(negate=Condition(field="gone", value="1")))

    assert clean is None


def test_no_expression_needs_no_validating():
    assert META.accept(None) == (None, [])
