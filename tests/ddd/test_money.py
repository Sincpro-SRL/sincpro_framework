"""`Money` behaves as Python expects a number to — adds, sums, compares, hashes, rounds, formats —
within one currency, exact until rounded on purpose, a bare number read as an amount of its own
currency; and refuses what has no meaning: two currencies added, a rate of neither."""

from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal

import pytest
from pydantic import ValidationError

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.ddd import (
    Currency,
    CurrencyMismatch,
    ExchangeRate,
    ExchangeRates,
    InMemoryExchangeRates,
    Money,
)
from sincpro_framework.ddd.money import ISO_CURRENCIES
from sincpro_framework.observability.metrics import InMemoryRecorder, field_path, metrics
from sincpro_framework.runtime.testing import ExchangeRatesContract

BTC = Currency("BTC", 8)
ETH = Currency("ETH", 18)


class Crypto(Money):
    """A money of a project's own: ISO 4217's currencies and two more."""

    currencies = {**ISO_CURRENCIES, "BTC": BTC, "ETH": ETH}


def test_an_amount_is_exact_and_equal_by_value():
    assert Money("10.50", "BOB") == Money("10.5", Currency("BOB"))
    assert {Money("10.50", "BOB"), Money("10.5", "BOB")} == {Money("10.5", "BOB")}
    assert Money("10.50", "BOB") != Money("10.50", "USD")
    big = Money("123456789012.123456789012345678", ETH)  # thirty digits: past decimal's 28
    assert (big * 3).amount == Decimal("370370367036.370370367037037034")


def test_a_float_or_an_int_is_read_by_the_digits_it_prints():
    assert Money(0.1, "BOB").amount == Decimal("0.1")
    assert Money(0.1, "BOB") + 0.2 == Money("0.3", "BOB")  # never 0.30000000000000004
    assert Money(10, "BOB") == 10 and Money("10.50", "BOB") * 1.13 == Money("11.865", "BOB")


def test_a_bare_number_is_an_amount_of_the_money_s_own_currency():
    price = Money("10.50", "BOB")

    assert sum([price, price, Money("0.75", "BOB")]) == Money("21.75", "BOB")
    assert price + 100 == 100 + price == Money("110.50", "BOB")
    assert 20 - price == Money("9.50", "BOB")
    assert price > 0 and price <= 10.5 and max(price, Money("3", "BOB")) is price


def test_two_currencies_are_never_added_nor_ordered_and_are_never_equal():
    price = Money("10.50", "BOB")

    with pytest.raises(CurrencyMismatch):
        assert price + Money("1", "USD")
    with pytest.raises(CurrencyMismatch):
        assert price < Money("1", "USD")
    assert price != Money("10.50", "USD")


@pytest.mark.parametrize(
    "operation",
    [lambda m: m + "1", lambda m: m * m, lambda m: 3 / m, lambda m: m + True],
)
def test_what_has_no_meaning_is_a_type_error(operation):
    with pytest.raises(TypeError):
        operation(Money("10.50", "BOB"))


def test_division_by_a_number_is_money_and_by_money_a_share():
    total = Money("32.25", "BOB")

    assert total / 4 == Money("8.0625", "BOB")
    assert round(Money("10.50", "BOB") / total, 4) == Decimal("0.3256")


def test_rounding_is_on_purpose_and_by_the_currency():
    iva = Money("32.25", "BOB") * Decimal("0.13")  # 4.1925

    assert (round(iva), round(iva, 3)) == (Money("4.19", "BOB"), Money("4.193", "BOB"))
    assert round(Money("2.5", "JPY")) == Money("3", "JPY")
    assert round(Money("1.0005", "KWD")) == Money("1.001", "KWD")
    assert Money("0.125", "BOB").rounded(ROUND_HALF_EVEN) == Money("0.12", "BOB")
    assert Money("10.03", "BOB").rounded(to="0.05") == Money("10.05", "BOB")  # cash


def test_zero_is_read_at_the_currency_s_decimals():
    assert not Money("0.001", "BOB") and Money("0.001", "BOB").is_zero
    assert Money("0.001", BTC).is_positive and Money("-0.01", "BOB").is_negative


def test_it_is_written_as_people_and_libraries_read_it():
    iva = Money("4.1925", "BOB")

    assert (str(iva), repr(iva)) == ("4.1925 BOB", "Money('4.1925', 'BOB')")
    assert f"{iva:.2f}" == "4.19 BOB" and f"{Money('0.125', 'BOB'):.2f}" == "0.13 BOB"
    assert f"{Money('1234567.5', 'BOB'):,.2f}" == "1,234,567.50 BOB"
    assert float(iva) == 4.1925
    assert Money.parse("10.50 BOB") == Money.parse("BOB 10.50") == Money("10.50", "BOB")


def test_it_travels_as_json_with_the_amount_a_string():
    price = Money("10.50", "BOB")

    assert price.model_dump(mode="json") == {"amount": "10.50", "currency": "BOB"}
    assert Money.model_validate_json('{"amount": 10.5, "currency": "bob"}') == price

    class ResponseInvoice(DataTransferObject):
        total: Money

    schema = ResponseInvoice.model_json_schema()["$defs"]["Money"]["properties"]
    assert schema["currency"]["type"] == "string"


def test_an_allocation_loses_no_cent_and_invents_none():
    assert Money("100", "BOB").allocate(1, 1, 1) == [
        Money("33.34", "BOB"),
        Money("33.33", "BOB"),
        Money("33.33", "BOB"),
    ]
    assert Money("100", "BOB").allocate(70, 30) == [Money("70", "BOB"), Money("30", "BOB")]
    assert sum(Money("-100", "BOB").split(3)) == Money("-100", "BOB")
    assert Money("10", "JPY").split(3) == [Money(4, "JPY"), Money(3, "JPY"), Money(3, "JPY")]
    with pytest.raises(ValueError):
        Money("100", "BOB").allocate(0, 0)


def test_a_rate_converts_either_way_and_refuses_a_currency_of_neither():
    usd_to_bob = ExchangeRate("USD", "BOB", "6.96", on=date(2026, 10, 27), source="BCB")

    assert Money("100", "USD").convert(usd_to_bob) == Money("696.00", "BOB")
    assert Money("696", "BOB").convert(usd_to_bob) == Money("100", "USD")
    assert Money("696", "BOB").convert(usd_to_bob.inverse()) == Money("100", "USD")
    assert Money("1", "USD").convert(usd_to_bob, rounded=False).amount == Decimal("6.96")
    with pytest.raises(CurrencyMismatch):
        Money("1", "EUR").convert(usd_to_bob)


def test_a_currency_of_a_project_s_own_carries_its_own_decimals():
    wei = Money("0.000000000000000001", ETH)

    assert (wei.currency.minor_units, wei.is_positive) == (18, True)
    assert round(Money("0.123456789", BTC)) == Money("0.12345679", BTC)
    with pytest.raises(ValueError):
        Currency("XYZ")  # not ISO 4217: its decimals are given


def test_a_subclass_reads_its_own_codes_from_json():
    sent = Money("0.5", BTC).model_dump(mode="json")

    assert sent == {"amount": "0.5", "currency": "BTC"}
    assert Crypto.model_validate(sent).currency is BTC
    assert Crypto.model_validate({"amount": "1", "currency": "eth"}).currency is ETH
    with pytest.raises(ValidationError):
        Money.model_validate(sent)  # the plain money reads ISO 4217's codes only


def test_over_many_a_total_a_total_by_currency_and_an_average():
    amounts = [Money("10", "BOB"), Money("5", "USD"), Money("20", "BOB")]

    assert Money.totals(amounts) == {"BOB": Money("30", "BOB"), "USD": Money("5", "USD")}
    assert Money.total([], "BOB") == Money.zero("BOB")
    assert Money.average(amounts[::2]) == Money("15", "BOB")
    with pytest.raises(CurrencyMismatch):
        Money.total(amounts)


def test_a_metric_of_money_is_one_series_per_currency():
    class CommandCharge(DataTransferObject):
        amount: int

    class ResponseCharge(DataTransferObject):
        total: Money

    billing = UseFramework("money-billing", log_after_execution=False)

    @billing.feature(CommandCharge)
    @metrics.sums(field_path(ResponseCharge).total)
    class Charge(Feature):
        def execute(self, dto: CommandCharge) -> ResponseCharge:
            return ResponseCharge(total=Money(dto.amount, "BOB" if dto.amount < 5 else "USD"))

    recorder = InMemoryRecorder()
    with metrics.using(recorder):
        for amount in (2, 3, 7):
            billing(CommandCharge(amount=amount))

    by_currency = {
        dict(labels)["currency"]: value
        for labels, value in recorder.totals("money_billing.charge.total").items()
    }
    assert by_currency == {"BOB": 5.0, "USD": 7.0}


class TestInMemoryExchangeRates(ExchangeRatesContract):
    def make_rates(self, rates) -> ExchangeRates:
        return InMemoryExchangeRates(rates)
