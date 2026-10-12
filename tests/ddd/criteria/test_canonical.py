"""The one form of a filter: what does not change the rows does not change the filter."""

from decimal import Decimal

from sincpro_framework.ddd.criteria import All, Any, Condition, Not, Operator, canonical

A = Condition(field="a", value=1)
B = Condition(field="b", operator=Operator.GT, value=2)


def test_the_parts_of_a_group_are_in_a_fixed_order():
    assert canonical(All(all=[B, A])) == canonical(All(all=[A, B]))
    assert canonical(Any(any=[B, A])) == canonical(Any(any=[A, B]))


def test_it_reaches_the_groups_inside_a_not():
    assert canonical(Not(negate=All(all=[B, A]))) == canonical(Not(negate=All(all=[A, B])))


def test_the_items_of_an_in_are_in_a_fixed_order():
    wide = Condition(field="a", operator=Operator.IN, value=[3, 1, 2])
    other = Condition(field="a", operator=Operator.IN, value=[1, 2, 3])

    assert canonical(wide) == canonical(other)


def test_a_decimal_is_its_value_and_not_its_zeros():
    assert canonical(Condition(field="amount", value=Decimal("1.00"))) == canonical(
        Condition(field="amount", value=Decimal("1.0"))
    )


def test_what_asks_other_rows_stays_other():
    assert canonical(A) != canonical(B)
    assert canonical(All(all=[A, B])) != canonical(Any(any=[A, B]))


def test_nothing_is_nothing():
    assert canonical(None) is None
