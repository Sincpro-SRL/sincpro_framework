"""What two equal parameters hash to. A key that differs for equal parameters makes a cache that
never hits and an idempotency that lets the retry write twice; one that matches for different
parameters hands one caller's answer to another."""

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum

from sincpro_framework import DataTransferObject
from sincpro_framework.data_layer.caching import KeyEncoder


class Currency(Enum):
    BOB = "BOB"


class Amount(DataTransferObject):
    value: Decimal
    currency: Currency


def test_equal_parameters_written_differently_are_one_key():
    assert KeyEncoder.key_of(Decimal("1.0")) == KeyEncoder.key_of(Decimal("1"))
    assert KeyEncoder.key_of({"a": 1, "b": 2}) == KeyEncoder.key_of({"b": 2, "a": 1})
    assert KeyEncoder.key_of({"x", "y", "z"}) == KeyEncoder.key_of({"z", "x", "y"})
    assert KeyEncoder.key_of(Currency.BOB) == KeyEncoder.key_of("BOB")
    assert KeyEncoder.key_of(
        Amount(value=Decimal("10.50"), currency=Currency.BOB)
    ) == KeyEncoder.key_of({"currency": "BOB", "value": Decimal("10.5")})
    assert KeyEncoder.key_of(date(2026, 9, 29)) == KeyEncoder.key_of("2026-09-29")


def test_different_parameters_are_different_keys():
    assert KeyEncoder.key_of("tenant", "a") != KeyEncoder.key_of("tenant", "b")
    assert KeyEncoder.key_of(("a", "bc")) != KeyEncoder.key_of(("ab", "c"))
    assert KeyEncoder.key_of(datetime(2026, 9, 29, 12, tzinfo=UTC)) != KeyEncoder.key_of(
        datetime(2026, 9, 29, 13, tzinfo=UTC)
    )
    assert KeyEncoder.key_of([1, 2]) != KeyEncoder.key_of([2, 1])


def test_a_key_is_a_fixed_length_hash():
    assert len(KeyEncoder.key_of("x" * 10_000)) == 32
