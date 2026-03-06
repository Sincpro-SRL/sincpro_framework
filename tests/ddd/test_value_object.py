"""Tests básicos de sincpro_framework.ddd.value_object."""

from typing import NewType

import pytest

from sincpro_framework.ddd.value_object import new_value_object
from tests.ddd.conftest import AmountVO, EmailVO, TagVO, UserIdVO

# ---------------------------------------------------------------------------
# Construction & identity
# ---------------------------------------------------------------------------


class TestValueObjectConstruction:
    def test_creates_instance_of_primitive_type(self):
        uid = UserIdVO(42)
        assert isinstance(uid, int)

    def test_validate_fn_executes_on_construction(self):
        uid = UserIdVO(-99)
        assert uid == 99  # abs() aplicado por validate_fn

    def test_string_value_object(self):
        email = EmailVO("  User@Example.COM  ")
        assert isinstance(email, str)

    def test_float_value_object(self):
        amount = AmountVO(19.999)
        assert isinstance(amount, float)


# ---------------------------------------------------------------------------
# Validation / transformation via validate_fn
# ---------------------------------------------------------------------------


class TestValidationFunction:
    def test_validate_fn_transforms_value(self):
        """EmailVO strips and lowercases via validate_fn."""
        email = EmailVO("  User@Example.COM  ")
        assert email == "user@example.com"

    def test_validate_fn_rounds_float(self):
        amount = AmountVO(3.14159)
        assert amount == 3.14

    def test_validate_fn_none_keeps_original_value(self):
        """When validate_fn returns None, the original value is kept."""
        tag = TagVO("python")
        assert tag == "python"

    def test_validate_fn_raises_on_invalid(self):
        with pytest.raises(ValueError, match="empty"):
            TagVO("   ")

    def test_int_vo_validate_fn_makes_absolute(self):
        """UserIdVO aplica abs() — un negativo se convierte en positivo."""
        uid = UserIdVO(-7)
        assert uid == 7


# ---------------------------------------------------------------------------
# __repr__
# ---------------------------------------------------------------------------


class TestRepr:
    def test_repr_uses_type_name(self):
        uid = UserIdVO(1)
        assert repr(uid) == "UserId(1)"

    def test_repr_str_value_object(self):
        email = EmailVO("hi@example.com")
        assert repr(email) == "Email('hi@example.com')"


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


class TestMetadata:
    def test_class_name_matches_newtype(self):
        assert UserIdVO.__name__ == "UserId"

    def test_qualname_matches_newtype(self):
        assert UserIdVO.__qualname__ == "UserId"

    def test_module_matches_newtype(self):
        assert UserIdVO.__module__ == "tests.ddd.conftest"


# ---------------------------------------------------------------------------
# Behaviour as primitive (operations)
# ---------------------------------------------------------------------------


class TestPrimitiveBehaviour:
    def test_int_arithmetic(self):
        uid = UserIdVO(10)
        assert uid + 5 == 15

    def test_str_concatenation(self):
        email = EmailVO("a@b.com")
        assert email + "!" == "a@b.com!"

    def test_equality_with_primitive(self):
        uid = UserIdVO(-42)
        assert uid == 42  # abs() via validate_fn

    def test_used_as_dict_key(self):
        uid = UserIdVO(-1)
        mapping = {uid: "found"}
        assert mapping[1] == "found"  # abs() → 1


# ---------------------------------------------------------------------------
# Multiple instances are independent
# ---------------------------------------------------------------------------


class TestIndependentInstances:
    def test_two_instances_same_value_are_equal(self):
        a = UserIdVO(5)
        b = UserIdVO(5)
        assert a == b

    def test_two_instances_different_value_are_not_equal(self):
        a = UserIdVO(1)
        b = UserIdVO(2)
        assert a != b

    def test_different_value_object_types_independent(self):
        """Dos VOs con distinto NewType y distinto validate_fn son independientes."""
        OrderId = NewType("OrderId", int)
        OrderIdVO = new_value_object(OrderId, lambda v: v * 2)  # duplica

        uid = UserIdVO(-3)  # abs(-3) = 3
        oid = OrderIdVO(3)  # 3 * 2 = 6

        assert uid == 3
        assert oid == 6
        assert type(uid).__name__ == "UserId"
        assert type(oid).__name__ == "OrderId"
