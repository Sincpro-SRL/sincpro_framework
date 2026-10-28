"""`ExchangeRatesContract`: the behaviour every `ExchangeRates` owes, as a test class to inherit.

    class TestCentralBankRates(ExchangeRatesContract):
        def make_rates(self, rates):
            return CentralBankRates(table_holding(rates))

An adapter of yours — a table, a provider's API — passes the same class. Nothing here imports
pytest.
"""

from collections.abc import Iterable
from datetime import date
from decimal import Decimal

from sincpro_framework.ddd import ExchangeRate, ExchangeRates


class ExchangeRatesContract:
    """Inherit, answer `make_rates` with an adapter holding the rates given, and run with pytest."""

    def make_rates(self, rates: Iterable[ExchangeRate]) -> ExchangeRates:
        """Answer an adapter that holds `rates` and nothing else."""
        raise NotImplementedError("answer an adapter holding the rates given")

    def test_the_latest_rate_on_or_before_the_day_is_answered(self) -> None:
        """A day between two rates answers the earlier one; a day before all, nothing."""
        rates = self.make_rates(
            [
                ExchangeRate("USD", "BOB", "6.96", on=date(2026, 10, 1)),
                ExchangeRate("USD", "BOB", "6.97", on=date(2026, 10, 20)),
            ]
        )
        found = rates.on("USD", "BOB", date(2026, 10, 19))
        if found is None or found.rate != Decimal("6.96"):
            raise AssertionError(f"the rate of 2026-10-19 came back as {found!r}")
        if rates.on("USD", "BOB", date(2026, 9, 30)) is not None:
            raise AssertionError("a day before every rate answered one")

    def test_a_rate_answers_the_other_way_too(self) -> None:
        """A rate stated USD→BOB answers BOB→USD as its inverse."""
        rates = self.make_rates([ExchangeRate("USD", "BOB", "6.96", on=date(2026, 10, 1))])
        found = rates.on("BOB", "USD", date(2026, 10, 27))
        if found is None or (found.base, found.quote) != ("BOB", "USD"):
            raise AssertionError(f"BOB→USD came back as {found!r}")
        if abs(found.rate * Decimal("6.96") - 1) > Decimal("1e-20"):
            raise AssertionError(f"the inverse rate {found.rate} is not 1 / 6.96")

    def test_a_pair_nobody_stated_is_none(self) -> None:
        """No rate of the pair: `None`, never a rate of another pair."""
        rates = self.make_rates([ExchangeRate("USD", "BOB", "6.96", on=date(2026, 10, 1))])
        if rates.on("USD", "EUR", date(2026, 10, 27)) is not None:
            raise AssertionError("a pair nobody stated answered a rate")
