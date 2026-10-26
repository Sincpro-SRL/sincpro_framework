"""The one form of a filter: what does not change the rows does not change the filter."""

from decimal import Decimal

from sincpro_framework.ddd.criteria import (
    All,
    Any,
    Condition,
    Not,
    Operator,
)

A = Condition(field="a", value=1)
B = Condition(field="b", operator=Operator.GT, value=2)


def test_the_parts_of_a_group_are_in_a_fixed_order():
    assert All(all=[B, A]).canonical() == All(all=[A, B]).canonical()
    assert Any(any=[B, A]).canonical() == Any(any=[A, B]).canonical()


def test_it_reaches_the_groups_inside_a_not():
    assert Not(negate=All(all=[B, A])).canonical() == Not(negate=All(all=[A, B])).canonical()


def test_the_items_of_an_in_are_in_a_fixed_order():
    wide = Condition(field="a", operator=Operator.IN, value=[3, 1, 2])
    other = Condition(field="a", operator=Operator.IN, value=[1, 2, 3])

    assert wide.canonical() == other.canonical()


def test_a_decimal_is_its_value_and_not_its_zeros():
    assert (
        Condition(field="amount", value=Decimal("1.00")).canonical()
        == Condition(field="amount", value=Decimal("1.0")).canonical()
    )


def test_what_asks_other_rows_stays_other():
    assert A.canonical() != B.canonical()
    assert All(all=[A, B]).canonical() != Any(any=[A, B]).canonical()
