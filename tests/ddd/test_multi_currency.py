"""`MultiCurrency`: an amount as it happened and its equivalents, each with the rate it was converted
by, kept as stated — read as a translated text is read, added line by line, stored as one JSON
document a database sums by currency."""

from datetime import date

import pytest
from pydantic import ValidationError
from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    Numeric,
    Table,
    cast,
    create_engine,
    func,
    insert,
    select,
)

from sincpro_framework.data_layer.orm.sqlalchemy.domain.custom_fields import TypedJson
from sincpro_framework.ddd import (
    Currency,
    CurrencyMismatch,
    ExchangeRate,
    Money,
    MultiCurrency,
)
from sincpro_framework.ddd.money import ISO_CURRENCIES

USD_TO_BOB = ExchangeRate("USD", "BOB", "6.96", on=date(2026, 10, 1), source="BCB")
EUR_TO_USD = ExchangeRate("EUR", "USD", "1.0858", on=date(2026, 10, 1), source="ECB")


def _paid() -> MultiCurrency:
    return MultiCurrency(Money("100", "USD")).at(USD_TO_BOB, EUR_TO_USD)


def test_each_equivalent_is_read_by_its_currency_as_a_translation_by_its_language():
    paid = _paid()

    assert (paid.origin, paid["BOB"], paid["eur"]) == (
        Money("100", "USD"),
        Money("696.00", "BOB"),
        Money("92.10", "EUR"),
    )
    assert paid["USD"] is paid.origin and paid.codes == ("USD", "BOB", "EUR")
    assert "BOB" in paid and "JPY" not in paid and len(paid) == 3
    assert paid.get("JPY") is None and list(paid)[1] == Money("696.00", "BOB")
    with pytest.raises(KeyError):
        assert paid["JPY"]


def test_a_rate_is_kept_as_it_was_stated_never_inverted():
    paid = _paid()

    assert paid.rate("BOB") == USD_TO_BOB
    assert paid.rate("EUR") == EUR_TO_USD  # published EUR→USD, kept so
    assert paid.rate("USD") is None


def test_an_equivalent_set_by_hand_is_a_price_with_no_rate():
    price = MultiCurrency(Money("10", "USD")).at(Money("70", "BOB"))

    assert (price["BOB"], price.rate("BOB")) == (Money("70", "BOB"), None)
    with pytest.raises(CurrencyMismatch):
        price.at(Money("11", "USD"))


def test_revaluing_gives_a_new_value_and_never_touches_the_recorded_one():
    paid = _paid()
    closing = paid.at(ExchangeRate("USD", "BOB", "6.97", on=date(2026, 10, 31)))

    assert closing["BOB"] - paid["BOB"] == Money("1.00", "BOB")  # the exchange difference
    assert paid["BOB"] == Money("696.00", "BOB")


def test_lines_add_up_currency_by_currency_keeping_a_rate_they_share():
    lines = [_paid(), _paid()]

    total = MultiCurrency.total(lines)
    assert sum(lines) == total  # sum() too: 0 + a value is the value
    assert (total["USD"], total["BOB"], total["EUR"]) == (
        Money("200", "USD"),
        Money("1392.00", "BOB"),
        Money("184.20", "EUR"),
    )
    assert total.rate("BOB") == USD_TO_BOB
    mixed = _paid() + _paid().at(ExchangeRate("USD", "BOB", "6.97"))
    assert mixed.rate("BOB") is None  # two rates: none of them is the total's
    with pytest.raises(CurrencyMismatch):
        assert _paid() + MultiCurrency(Money("1", "USD"))


def test_it_travels_as_one_json_document_and_comes_back_equal():
    paid = _paid()

    written = paid.model_dump(mode="json")
    assert written["equivalents"]["EUR"] == {
        "amount": "92.10",
        "rate": "1.0858",
        "base": "EUR",
        "on": "2026-10-01",
        "source": "ECB",
    }
    assert MultiCurrency.model_validate(written) == paid
    assert hash(MultiCurrency.model_validate(written)) == hash(paid)
    with pytest.raises(ValidationError):
        MultiCurrency.model_validate({**written, "equivalents": {"USD": {"amount": "1"}}})


def test_a_subclass_holds_currencies_of_its_own():
    eth = Currency("ETH", 18)

    class Crypto(MultiCurrency):
        currencies = {**ISO_CURRENCIES, "ETH": eth}

    held = MultiCurrency(Money("0.5", eth)).at(Money("600", "USD"))
    assert Crypto.model_validate(held.model_dump(mode="json"))["ETH"] == Money("0.5", eth)


def test_stored_in_one_json_column_a_database_sums_it_by_currency():
    metadata = MetaData()
    invoices = Table(
        "invoice",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("total", TypedJson(MultiCurrency)),
    )
    engine = create_engine("sqlite://")
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(
            insert(invoices), [{"id": 1, "total": _paid()}, {"id": 2, "total": _paid()}]
        )
        read = connection.execute(select(invoices.c.total)).scalars().first()
        in_bob = cast(
            invoices.c.total["equivalents"]["BOB"]["amount"].as_string(), Numeric(38, 18)
        )
        summed = connection.execute(select(func.sum(in_bob))).scalar()

    assert read == _paid()
    assert summed == 1392
