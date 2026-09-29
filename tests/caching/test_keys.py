"""What two equal parameters hash to. A key that differs for equal parameters makes a cache that
never hits and an idempotency that lets the retry write twice; one that matches for different
parameters hands one caller's answer to another."""

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum

from sincpro_framework import DataTransferObject
from sincpro_framework.caching import key_of


class Currency(Enum):
    BOB = "BOB"


class Amount(DataTransferObject):
    value: Decimal
    currency: Currency


def test_equal_parameters_written_differently_are_one_key():
    assert key_of(Decimal("1.0")) == key_of(Decimal("1"))
    assert key_of({"a": 1, "b": 2}) == key_of({"b": 2, "a": 1})
    assert key_of({"x", "y", "z"}) == key_of({"z", "x", "y"})
    assert key_of(Currency.BOB) == key_of("BOB")
    assert key_of(Amount(value=Decimal("10.50"), currency=Currency.BOB)) == key_of(
        {"currency": "BOB", "value": Decimal("10.5")}
    )
    assert key_of(date(2026, 9, 29)) == key_of("2026-09-29")


def test_different_parameters_are_different_keys():
    assert key_of("tenant", "a") != key_of("tenant", "b")
    assert key_of(("a", "bc")) != key_of(("ab", "c"))
    assert key_of(datetime(2026, 9, 29, 12, tzinfo=UTC)) != key_of(
        datetime(2026, 9, 29, 13, tzinfo=UTC)
    )
    assert key_of([1, 2]) != key_of([2, 1])


def test_a_key_is_a_fixed_length_hash():
    assert len(key_of("x" * 10_000)) == 32
