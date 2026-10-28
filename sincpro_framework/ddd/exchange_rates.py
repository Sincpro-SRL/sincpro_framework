"""`ExchangeRates`: where the rates of a date are found — the port an ERP fills with its table, its
daily load and the source it trusts — and `InMemoryExchangeRates`, the one a test or a small
service holds.

    rates = InMemoryExchangeRates([ExchangeRate("USD", "BOB", "6.96", on=date(2026, 10, 1))])
    rates.on("USD", "BOB", date(2026, 10, 27))   ->  the rate of 2026-10-01: the latest on or before
    rates.on("BOB", "USD", date(2026, 10, 27))   ->  its inverse
    rates.on("USD", "EUR", date(2026, 10, 27))   ->  None: nobody said

Context: a rate is a fact of a date and a source, so the framework never holds rates of its own —
which source counts, per company or tenant, is the adapter's to know (its options, or the
context it reads). The port is the one question every store answers.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterable
from datetime import date

from sincpro_framework.ddd.money import ISO_CURRENCIES, Currency, ExchangeRate, currency_of


class ExchangeRates(ABC):
    """The rates of a date."""

    @abstractmethod
    def on(
        self, base: Currency | str, quote: Currency | str, day: date
    ) -> ExchangeRate | None:
        """What one `base` is worth in `quote` on `day`: the latest rate on or before it, either
        way it was stated — `None` when there is none."""


class InMemoryExchangeRates(ExchangeRates):
    """Rates held by this process — for tests, a script, a service with few rates."""

    def __init__(self, rates: Iterable[ExchangeRate] = ()) -> None:
        self.rates: list[ExchangeRate] = list(rates)

    def add(self, rate: ExchangeRate) -> None:
        """One more rate, of its date."""
        self.rates.append(rate)

    def on(
        self, base: Currency | str, quote: Currency | str, day: date
    ) -> ExchangeRate | None:
        """1. The rates of the pair either way, on or before `day` — one with no date, always.
        Final: the latest of them, turned to `base` in `quote`."""
        base = currency_of(base, ISO_CURRENCIES)
        quote = currency_of(quote, ISO_CURRENCIES)
        held = [
            one
            for one in self.rates
            if {one.base, one.quote} == {base, quote} and (one.on is None or one.on <= day)
        ]
        if not held:
            return None
        latest = max(held, key=lambda one: one.on or date.min)
        return latest if latest.base == base else latest.inverse()
