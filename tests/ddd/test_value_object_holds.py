"""What a value object holds, written as Python over its value and read once into the language:
checked on every construction, after its Python rule, and published for a client to check offline.
A value object with only a Python rule works as it always did."""

from dataclasses import dataclass
from decimal import Decimal

import pytest
from pydantic import ValidationError

from sincpro_framework import DataTransferObject
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import Condition, Entity, ValueObject
from sincpro_framework.ddd.criteria import All, Operator

Discount = ValueObject(Decimal, name="Discount", holds=lambda v: 0 <= v <= 30)
Size = ValueObject(str, name="Size", holds=lambda v: v in ["S", "M", "L"])
Positive = ValueObject(int, lambda v: abs(v), name="Positive", holds=lambda v: v > 0)
UserId = ValueObject(int, lambda v: abs(v), name="UserId")


def test_a_value_that_holds_is_built_as_its_primitive():
    assert Discount(Decimal("10")) == Decimal("10")
    assert isinstance(Discount(Decimal("10")), Decimal)


def test_a_value_that_does_not_hold_is_refused_with_the_rule_as_written():
    with pytest.raises(
        ValueError, match=r"Discount holds `0 <= v <= 30`; Decimal\('45'\) does not"
    ):
        Discount(Decimal("45"))


def test_the_python_rule_runs_first_and_the_value_it_answers_is_judged():
    assert Positive(-7) == 7
    with pytest.raises(ValueError, match="Positive holds `v > 0`"):
        Positive(0)


def test_a_value_object_with_only_a_python_rule_works_as_it_always_did():
    assert UserId(-5) == 5


class CommandPrice(DataTransferObject):
    discount: Discount
    size: Size


def test_a_command_refuses_a_value_that_does_not_hold():
    assert CommandPrice(discount=Decimal("5"), size="M").size == "M"
    with pytest.raises(ValidationError, match="Size holds"):
        CommandPrice(discount=Decimal("5"), size="XL")


def test_the_schema_says_what_it_holds_with_standard_keywords_and_whole():
    schema = CommandPrice.model_json_schema()["properties"]

    assert schema["discount"]["minimum"] == 0 and schema["discount"]["maximum"] == 30
    assert schema["size"]["enum"] == ["S", "M", "L"]
    assert schema["discount"]["x-sincpro"]["holds"] == {
        "all": [
            {"field": "value", "operator": ">=", "value": 0},
            {"field": "value", "operator": "<=", "value": 30},
        ]
    }


@dataclass
class Quote(Entity):
    discount: Discount = Discount(Decimal("0"))
    note: str = ""


def test_a_field_typed_with_the_value_object_publishes_what_it_holds():
    fields = MemoryRepository().definition(Quote).fields

    assert fields["discount"].holds == All(
        all=[
            Condition(field="value", operator=Operator.GTE, value=0),
            Condition(field="value", operator=Operator.LTE, value=30),
        ]
    )
    assert fields["note"].holds is None
