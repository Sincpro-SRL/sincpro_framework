"""`MultiCurrency`: an amount as it happened, and what it is worth in other currencies — each
equivalent with the rate it was converted by, frozen when it was recorded; one value, one JSON
document, as a text kept in several languages is.

    total = MultiCurrency(Money("100", "USD")).at(usd_to_bob, usd_to_eur)
    total.origin           Money('100', 'USD')            what happened, never changed
    total["BOB"]           Money('696.00', 'BOB')         as a translated text's title["es"]
    total.rate("BOB")      ExchangeRate('USD', 'BOB', '6.96', on=…, source='BCB')
    total.at(closing)      a new value: another equivalent, or one re-valued at another rate
    price.at(Money("70", "BOB"))     an equivalent set by hand — a price list — with no rate

Context: a ledger keeps an amount in the currency it happened in and its equivalents in the
company's, the group's, the reports' — each converted once, at its date, so every report adds the
same numbers and no query joins a table of rates. Stored, it is
`{"origin": {...}, "equivalents": {"BOB": {"amount": "696.00", "rate": "6.96", "on": …,
"base": "USD", "source": …}}}`: a column the database can sum by currency
(`total->'equivalents'->'BOB'->>'amount'`). A rate is kept as it was stated — its unit currency
with it, as ISO 20022 does — never inverted into digits nobody published.
"""

from collections.abc import Iterable, Iterator, Mapping
from datetime import date
from decimal import Decimal
from typing import Any, ClassVar, Self

from pydantic import BaseModel, ConfigDict, field_serializer, field_validator, model_validator

from sincpro_framework.ddd import exceptions
from sincpro_framework.ddd.money import (
    ISO_CURRENCIES,
    Currency,
    ExchangeRate,
    Money,
    currency_of,
)
from sincpro_framework.sincpro_abstractions import DataTransferObject


class Equivalent(DataTransferObject):
    """What the origin is worth in one other currency, and the rate it was converted by — no
    rate when it was set by hand."""

    model_config = ConfigDict(frozen=True)

    amount: Decimal
    rate: Decimal | None = None
    """The rate as it was stated — `None` for an amount set by hand."""
    base: str | None = None
    """The currency one unit of which the rate is stated for (ISO 20022's unit currency): the
    origin's, or this one's — so a rate published EUR→USD is kept as published."""
    on: date | None = None
    source: str | None = None

    @field_serializer("amount", "rate", when_used="json")
    def _written(self, value: Decimal | None) -> str | None:
        """In fixed point, as `Money` writes its amount."""
        return None if value is None else format(value, "f")


class MultiCurrency(DataTransferObject):
    """An amount in the currency it happened in, and its equivalents. See the module."""

    model_config = ConfigDict(frozen=True)

    origin: Money
    equivalents: dict[str, Equivalent] = {}
    """By currency code: what the origin is worth in each."""

    currencies: ClassVar[Mapping[str, Currency]] = ISO_CURRENCIES
    """What a code is read as — a subclass adds its own, as `Money`'s."""

    def __init__(
        self, origin: Money, equivalents: Mapping[str, Equivalent] | None = None
    ) -> None:
        BaseModel.__init__(self, origin=origin, equivalents=dict(equivalents or {}))

    @field_validator("origin", mode="before")
    @classmethod
    def _origin(cls, value: Any) -> Money:
        """The origin as a money whose currency is read among this class's currencies."""
        if isinstance(value, Money):
            return value
        if isinstance(value, Mapping):
            return Money(value["amount"], currency_of(value["currency"], cls.currencies))
        raise ValueError(f"an origin is a money, not {value!r}")

    @field_validator("equivalents", mode="before")
    @classmethod
    def _codes(cls, value: Any) -> Any:
        """Each currency by its code, in capitals, and one this class knows."""
        if not isinstance(value, Mapping):
            return value
        return {str(currency_of(code, cls.currencies)): one for code, one in value.items()}

    @model_validator(mode="after")
    def _not_its_own(self) -> Self:
        if str(self.origin.currency) in self.equivalents:
            raise ValueError(
                f"{self.origin.currency} is the origin, never an equivalent of it"
            )
        return self

    # reading it

    def currency(self, code: str) -> Currency:
        """The `Currency` `code` names among those this value holds."""
        found = currency_of(code, self.currencies)
        if found != self.origin.currency and str(found) not in self.equivalents:
            raise KeyError(f"{self} holds no {found}")
        return found if found != self.origin.currency else self.origin.currency

    def __getitem__(self, code: str) -> Money:
        """The amount in `code`: the origin for its own currency, else its equivalent —
        `KeyError` for a currency it holds none of."""
        currency = self.currency(code)
        if currency == self.origin.currency:
            return self.origin
        return Money(self.equivalents[str(currency)].amount, currency)

    def get(self, code: str, default: Money | None = None) -> Money | None:
        try:
            return self[code]
        except KeyError:
            return default

    def __contains__(self, code: object) -> bool:
        return isinstance(code, str) and (
            code.upper() == self.origin.currency or code.upper() in self.equivalents
        )

    def __iter__(self) -> Iterator[Money]:  # type: ignore[override]
        """Each amount it holds, the origin first."""
        yield self.origin
        for code in self.equivalents:
            yield self[code]

    def __len__(self) -> int:
        return 1 + len(self.equivalents)

    @property
    def codes(self) -> tuple[str, ...]:
        """The currencies it holds, the origin's first."""
        return (str(self.origin.currency), *self.equivalents)

    def rate(self, code: str) -> ExchangeRate | None:
        """The rate the equivalent in `code` was converted by, as it was stated — `None` for
        one set by hand; `KeyError` for a currency it holds none of."""
        currency = self.currency(code)
        if currency == self.origin.currency:
            return None
        held = self.equivalents[str(currency)]
        if held.rate is None:
            return None
        stated_by_origin = held.base is None or held.base == self.origin.currency
        base, quote = (
            (self.origin.currency, currency)
            if stated_by_origin
            else (currency, self.origin.currency)
        )
        return ExchangeRate(base, quote, held.rate, held.on, held.source)

    # adding equivalents

    def at(self, *conversions: ExchangeRate | Money) -> Self:
        """A new value with an equivalent for each — a rate converts the origin into its other
        currency (either way it is stated), a money is an amount set by hand; one already held
        is replaced, the origin never.

            MultiCurrency(Money("100", "USD")).at(usd_to_bob, Money("92", "EUR"))
        """
        equivalents = dict(self.equivalents)
        for one in conversions:
            if isinstance(one, Money):
                if one.currency == self.origin.currency:
                    raise exceptions.CurrencyMismatch(
                        f"{one} is in the origin's currency: it is never an equivalent"
                    )
                equivalents[str(one.currency)] = Equivalent(amount=one.amount)
                continue
            converted = self.origin.convert(one)
            equivalents[str(converted.currency)] = Equivalent(
                amount=converted.amount,
                rate=one.rate,
                base=str(one.base),
                on=one.on,
                source=one.source,
            )
        return type(self).model_construct(origin=self.origin, equivalents=equivalents)

    # comparing and adding

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, MultiCurrency):
            return NotImplemented
        return self.origin == other.origin and dict(zip(self.codes, self)) == dict(
            zip(other.codes, other)
        )

    def __hash__(self) -> int:
        return hash((self.origin, tuple(sorted(self.equivalents))))

    def __add__(self, other: Any) -> Self:
        """The sum of two, currency by currency — what a document's lines add up to in each.
        Both hold the same currencies (`CurrencyMismatch` otherwise); an equivalent keeps its
        rate only when both were converted by the same one."""
        if isinstance(other, int) and other == 0:
            return self
        if not isinstance(other, MultiCurrency):
            return NotImplemented
        if (
            set(self.codes) != set(other.codes)
            or self.origin.currency != other.origin.currency
        ):
            raise exceptions.CurrencyMismatch(
                f"{self} and {other} do not hold the same currencies"
            )
        equivalents = {}
        for code, mine in self.equivalents.items():
            theirs = other.equivalents[code]
            same = mine.model_copy(update={"amount": 0}) == theirs.model_copy(
                update={"amount": 0}
            )
            added = (self[code] + other[code]).amount
            equivalents[code] = (
                mine.model_copy(update={"amount": added})
                if same
                else Equivalent(amount=added)
            )
        return type(self).model_construct(
            origin=self.origin + other.origin, equivalents=equivalents
        )

    def __radd__(self, other: Any) -> Self:
        return self.__add__(other)

    @classmethod
    def total(cls, values: Iterable["MultiCurrency"]) -> Self:
        """Their sum, currency by currency — `ValueError` for none."""
        items = list(values)
        if not items:
            raise ValueError("a total of no amounts has no currencies")
        added = items[0]
        for one in items[1:]:
            added = added + one
        return added  # type: ignore[return-value]

    # reading it out

    def __str__(self) -> str:
        return " · ".join(str(amount) for amount in self)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({' · '.join(str(amount) for amount in self)})"
