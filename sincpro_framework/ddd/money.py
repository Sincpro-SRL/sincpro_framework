"""`Money`: an amount in its currency, that behaves as Python expects a number to behave — and
`Currency`, `ExchangeRate`, the values around it.

    price = Money("10.50", "BOB")
    total = sum(lines)                         Money: `0 + money` starts a sum
    total == Money("32.250", "BOB")            by value; hashable, so a dict key or a set member
    round(total * Decimal("0.13"))             Money('4.19', 'BOB'): exact until rounded
    price * 1.13, price + 100, price > 0       a bare number is an amount in the money's currency
    Money("100", "BOB").allocate(1, 1, 1)      [33.34, 33.33, 33.33]: no cent lost
    Money("100", "USD").convert(usd_to_bob)    Money('696.00', 'BOB')
    price + Money("1", "USD")                  CurrencyMismatch

Context: the amount is a `Decimal`, exact — every digit kept, in a context of 60 digits, until it
is rounded on purpose (`round`, `rounded`, `allocate`, `convert`). A float or an int read as an
amount is read by the digits it prints (`0.1` is `Decimal("0.1")`), so the amounts an ERP holds
as floats are taken as they were written. A currency is a `str` — its code — with the decimals its
amounts carry: those ISO 4217 publishes, or any a project gives (a crypto's 8 or 18, a bank's unit
of account). Two currencies are never added; a rate converts them. What a subclass changes: the
currencies a code is read as (`currencies`), the rounding (`rounding`), the precision
(`context`).
"""

import re
from collections.abc import Iterable, Mapping
from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_UP, Context, Decimal, localcontext
from typing import Any, ClassVar, Self

from pydantic import BaseModel, ConfigDict, field_validator
from pydantic_core import core_schema

from sincpro_framework.ddd import exceptions
from sincpro_framework.ddd.iso_4217 import ISO_4217
from sincpro_framework.sincpro_abstractions import DataTransferObject

PRECISION = Context(prec=60)
"""The digits an amount keeps through its arithmetic — past any crypto's or bank's."""

FIXED_POINT = re.compile(r"\.(\d+)f$")
"""A format spec that fixes the decimals — `.2f`, `,.4f` — rounded by the money's rule."""


def decimal_of(value: Any) -> Decimal | None:
    """`value` as an exact `Decimal` — a float by the digits it prints — or `None` when it is
    not a number (a `bool` is not one here).

        decimal_of(0.1)  ->  Decimal('0.1')       decimal_of("10.50")  ->  Decimal('10.50')
        decimal_of(True) ->  None                 decimal_of("ten")    ->  None
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(repr(value))
    if isinstance(value, str):
        try:
            return Decimal(value.strip().replace("_", ""))
        except ArithmeticError:
            return None
    return None


class Currency(str):
    """A currency: its code, as the `str` it is, and the decimals its amounts carry.

    Currency("BOB")               ->  Currency('BOB', 2)     ISO 4217's decimals
    Currency("BTC", 8)            ->  Currency('BTC', 8)     a currency of a project's own
    Currency("BOB") == "BOB"      ->  True                   a code compares as its text
    """

    minor_units: int

    def __new__(cls, code: str, minor_units: int | None = None) -> Self:
        """`code` in capitals, with `minor_units` — or, not given, what ISO 4217 says;
        `ValueError` for a code it does not hold."""
        code = code.strip().upper()
        units = ISO_4217.get(code) if minor_units is None else minor_units
        if units is None:
            raise ValueError(
                f"{code!r} is not an ISO 4217 currency: give its decimals, Currency({code!r}, 8)"
            )
        made = super().__new__(cls, code)
        made.minor_units = units
        return made

    def __repr__(self) -> str:
        return f"Currency({str(self)!r}, {self.minor_units})"

    @property
    def unit(self) -> Decimal:
        """Its smallest amount — `0.01` for BOB, `1` for JPY, `0.00000001` for BTC."""
        return Decimal(1).scaleb(-self.minor_units)

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type: Any, handler: Any) -> Any:
        """Validated from a `Currency` as it is, or from an ISO 4217 code; written as its code."""

        def validated(value: Any) -> "Currency":
            if isinstance(value, Currency):
                return value
            if isinstance(value, str):
                return cls(value)
            raise ValueError(f"a currency is its code, not {value!r}")

        return core_schema.no_info_plain_validator_function(
            validated, serialization=core_schema.to_string_ser_schema()
        )

    @classmethod
    def __get_pydantic_json_schema__(cls, schema: Any, handler: Any) -> dict[str, Any]:
        return {
            "type": "string",
            "title": "Currency",
            "description": "Its code: BOB, USD, BTC.",
        }


ISO_CURRENCIES: Mapping[str, Currency] = {code: Currency(code) for code in ISO_4217}
"""Every ISO 4217 currency, by its code — what a code is read as unless a subclass says more."""


def currency_of(value: Any, currencies: Mapping[str, Currency]) -> Currency:
    """`value` as a `Currency`: itself, a code among `currencies`, or `{"code", "minor_units"}`."""
    if isinstance(value, Currency):
        return value
    if isinstance(value, str):
        found = currencies.get(value.strip().upper())
        if found is None:
            raise ValueError(
                f"{value!r} is not a currency this money knows: pass Currency({value!r}, decimals), "
                "or add it to the subclass's `currencies`"
            )
        return found
    if isinstance(value, Mapping):
        return Currency(value["code"], value.get("minor_units"))
    raise ValueError(f"a currency is a Currency or its code, not {value!r}")


class ExchangeRate(DataTransferObject):
    """What one `base` is worth in `quote`, on a date, by a source — stored beside what it
    converted, so a conversion is never computed again with another day's rate.

        ExchangeRate("USD", "BOB", "6.96", on=date(2026, 10, 27), source="BCB")
        rate.inverse()   ->  BOB in USD, by the same rate
    """

    model_config = ConfigDict(frozen=True)

    base: Currency
    quote: Currency
    rate: Decimal
    """How much one `base` is in `quote` — more than zero."""
    on: date | None = None
    source: str | None = None
    """Who said it — a central bank, a provider, a desk."""

    currencies: ClassVar[Mapping[str, Currency]] = ISO_CURRENCIES

    def __init__(
        self,
        base: Currency | str,
        quote: Currency | str,
        rate: Decimal | str | int | float,
        on: date | None = None,
        source: str | None = None,
    ) -> None:
        BaseModel.__init__(self, base=base, quote=quote, rate=rate, on=on, source=source)

    @field_validator("base", "quote", mode="before")
    @classmethod
    def _currency(cls, value: Any) -> Currency:
        return currency_of(value, cls.currencies)

    @field_validator("rate", mode="before")
    @classmethod
    def _rate(cls, value: Any) -> Decimal:
        rate = decimal_of(value)
        if rate is None or not rate.is_finite() or rate <= 0:
            raise ValueError(f"a rate is a number more than zero, not {value!r}")
        return rate

    def inverse(self) -> "ExchangeRate":
        """The same rate the other way: `quote` in `base`."""
        with localcontext(PRECISION):
            return type(self)(self.quote, self.base, 1 / self.rate, self.on, self.source)


class Money(DataTransferObject):
    """An amount in its currency. See the module for what it does; its JSON is
    `{"amount": "10.50", "currency": "BOB"}`, the amount a string."""

    model_config = ConfigDict(frozen=True)

    amount: Decimal
    """Exact — every digit kept until it is rounded on purpose."""
    currency: Currency

    currencies: ClassVar[Mapping[str, Currency]] = ISO_CURRENCIES
    """What a code is read as — a subclass adds its own: `{**ISO_CURRENCIES, "BTC": ...}`."""
    rounding: ClassVar[str] = ROUND_HALF_UP
    """How `round`, `rounded`, `convert` and a fixed format round — a mode of `decimal`."""
    context: ClassVar[Context] = PRECISION
    """The digits its arithmetic keeps."""
    labelled_by: ClassVar[tuple[str, ...]] = ("currency",)
    """What a metric of it is labelled by: one series per currency, never summed across."""

    def __init__(self, amount: Decimal | str | int | float, currency: Currency | str) -> None:
        BaseModel.__init__(self, amount=amount, currency=currency)

    @field_validator("amount", mode="before")
    @classmethod
    def _amount(cls, value: Any) -> Decimal:
        amount = decimal_of(value)
        if amount is None or not amount.is_finite():
            raise ValueError(f"an amount is a finite number, not {value!r}")
        return amount

    @field_validator("currency", mode="before")
    @classmethod
    def _currency(cls, value: Any) -> Currency:
        return currency_of(value, cls.currencies)

    # building

    def _with(self, amount: Decimal) -> Self:
        """The same currency, another amount — already a `Decimal`, nothing to validate."""
        return type(self).model_construct(amount=amount, currency=self.currency)

    def _amount_of(self, other: Any) -> Decimal | None:
        """What `other` adds, subtracts or compares as: a money of this currency's amount, a
        bare number's — `CurrencyMismatch` for another currency, `None` for anything else."""
        if isinstance(other, Money):
            if other.currency != self.currency:
                raise exceptions.CurrencyMismatch(
                    f"{self} and {other} are of different currencies: convert one by a rate"
                )
            return other.amount
        return decimal_of(other) if not isinstance(other, str) else None

    @classmethod
    def zero(cls, currency: Currency | str) -> Self:
        """Nothing, in `currency` — where a sum of none starts."""
        return cls(0, currency)

    @classmethod
    def parse(cls, text: str) -> Self:
        """A money written as text — `"10.50 BOB"` or `"BOB 10.50"`."""
        parts = text.split()
        if len(parts) != 2:
            raise ValueError(f"{text!r} is not an amount and a currency")
        first, second = parts
        return cls(second, first) if decimal_of(first) is None else cls(first, second)

    # arithmetic — a bare number is an amount in this currency

    def __add__(self, other: Any) -> Self:
        amount = self._amount_of(other)
        if amount is None:
            return NotImplemented
        with localcontext(self.context):
            return self._with(self.amount + amount)

    def __radd__(self, other: Any) -> Self:
        return self.__add__(other)

    def __sub__(self, other: Any) -> Self:
        amount = self._amount_of(other)
        if amount is None:
            return NotImplemented
        with localcontext(self.context):
            return self._with(self.amount - amount)

    def __rsub__(self, other: Any) -> Self:
        amount = self._amount_of(other)
        if amount is None:
            return NotImplemented
        with localcontext(self.context):
            return self._with(amount - self.amount)

    def __mul__(self, other: Any) -> Self:
        factor = None if isinstance(other, (Money, str)) else decimal_of(other)
        if factor is None:
            return NotImplemented
        with localcontext(self.context):
            return self._with(self.amount * factor)

    def __rmul__(self, other: Any) -> Self:
        return self.__mul__(other)

    def __truediv__(self, other: Any) -> Any:
        """By a number: a money. By a money of this currency: how many times it fits, a
        `Decimal` — a share of a total."""
        with localcontext(self.context):
            if isinstance(other, Money):
                share = self._amount_of(other)
                return NotImplemented if share is None else self.amount / share
            divisor = None if isinstance(other, str) else decimal_of(other)
            if divisor is None:
                return NotImplemented
            return self._with(self.amount / divisor)

    def __neg__(self) -> Self:
        return self._with(-self.amount)

    def __pos__(self) -> Self:
        return self

    def __abs__(self) -> Self:
        return self._with(abs(self.amount))

    # comparing — by value, within one currency

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Money):
            return self.currency == other.currency and self.amount == other.amount
        amount = None if isinstance(other, str) else decimal_of(other)
        return NotImplemented if amount is None else self.amount == amount

    def __hash__(self) -> int:
        return hash(self.amount)

    def __lt__(self, other: Any) -> bool:
        amount = self._amount_of(other)
        return NotImplemented if amount is None else self.amount < amount

    def __le__(self, other: Any) -> bool:
        amount = self._amount_of(other)
        return NotImplemented if amount is None else self.amount <= amount

    def __gt__(self, other: Any) -> bool:
        amount = self._amount_of(other)
        return NotImplemented if amount is None else self.amount > amount

    def __ge__(self, other: Any) -> bool:
        amount = self._amount_of(other)
        return NotImplemented if amount is None else self.amount >= amount

    # rounding — on purpose, by the currency

    def rounded(self, mode: str | None = None, to: Decimal | str | int | None = None) -> Self:
        """To the currency's decimals — or to a step, `to=Decimal("0.05")` for cash — by
        `mode`, a rounding of `decimal`, else the money's `rounding`."""
        mode = mode or self.rounding
        with localcontext(self.context):
            if to is None:
                return self._with(self.amount.quantize(self.currency.unit, rounding=mode))
            step = decimal_of(to)
            if step is None or step <= 0:
                raise ValueError(f"a step to round to is more than zero, not {to!r}")
            return self._with((self.amount / step).quantize(Decimal(1), rounding=mode) * step)

    def __round__(self, ndigits: int | None = None) -> Self:
        """`round(m)` to the currency's decimals, `round(m, n)` to `n` of them."""
        if ndigits is None:
            return self.rounded()
        with localcontext(self.context):
            return self._with(
                self.amount.quantize(Decimal(1).scaleb(-ndigits), rounding=self.rounding)
            )

    @property
    def is_zero(self) -> bool:
        """Zero at the currency's decimals — `0.001` BOB is none, as an accountant reads it."""
        return self.rounded().amount == 0

    @property
    def is_negative(self) -> bool:
        return self.rounded().amount < 0

    @property
    def is_positive(self) -> bool:
        return self.rounded().amount > 0

    def __bool__(self) -> bool:
        return not self.is_zero

    # splitting and converting

    def allocate(self, *ratios: Decimal | int | float) -> list[Self]:
        """Parts by `ratios` that add up to it, rounded to the currency, exactly — what is left
        of the rounding, a unit at a time, to the first parts.

            Money("100", "BOB").allocate(1, 1, 1)  ->  [33.34, 33.33, 33.33]
            Money("100", "BOB").allocate(70, 30)   ->  [70.00, 30.00]

        1. The amount rounded to the currency; each ratio a number of zero or more, not all zero.
        2. Each part its share, cut down to the currency's unit.
        Final: the units the cuts left, one each to the first parts with a share.
        """
        weights = [decimal_of(ratio) for ratio in ratios]
        if not weights or any(w is None or w < 0 for w in weights) or not any(weights):
            raise ValueError(f"ratios are numbers of zero or more, not all zero: {ratios!r}")
        whole = self.rounded().amount
        unit = self.currency.unit if whole >= 0 else -self.currency.unit
        with localcontext(self.context):
            total = sum(w for w in weights if w is not None)
            parts = [
                (whole * (w or 0) / total).quantize(self.currency.unit, rounding=ROUND_DOWN)
                for w in weights
            ]
            left = int((whole - sum(parts)) / unit)
            for index, weight in enumerate(weights):
                if left and weight:
                    parts[index] += unit
                    left -= 1
        return [self._with(part) for part in parts]

    def split(self, parts: int) -> list[Self]:
        """`parts` equal parts that add up to it — `Money("100", "BOB").split(3)`."""
        return self.allocate(*([1] * parts))

    def convert(self, rate: ExchangeRate, rounded: bool = True) -> "Money":
        """Into the rate's other currency — `rate.base` to `rate.quote`, or back — rounded to
        it unless `rounded=False`; `CurrencyMismatch` for a rate of neither."""
        with localcontext(self.context):
            if self.currency == rate.base:
                converted = type(self).model_construct(
                    amount=self.amount * rate.rate, currency=rate.quote
                )
            elif self.currency == rate.quote:
                converted = type(self).model_construct(
                    amount=self.amount / rate.rate, currency=rate.base
                )
            else:
                raise exceptions.CurrencyMismatch(
                    f"{self} is not converted by a rate of {rate.base} to {rate.quote}"
                )
        return converted.rounded() if rounded else converted

    # reading it out

    def __float__(self) -> float:
        """The amount as a float — for a chart, a model, a library that wants one; never
        stored or added back."""
        return float(self.amount)

    def __str__(self) -> str:
        return f"{self.amount} {self.currency}"

    def __repr__(self) -> str:
        return f"{type(self).__name__}('{self.amount}', '{self.currency}')"

    def __format__(self, spec: str) -> str:
        """`f"{m:.2f}"` → `"4.19 BOB"`: the amount formatted, rounded by the money's rule when
        the spec fixes its decimals, and the code after it."""
        if not spec:
            return str(self)
        amount = self.amount
        fixed = FIXED_POINT.search(spec)
        if fixed:
            amount = amount.quantize(
                Decimal(1).scaleb(-int(fixed.group(1))), rounding=self.rounding
            )
        return f"{format(amount, spec)} {self.currency}"

    # over many

    @classmethod
    def total(
        cls, amounts: Iterable["Money"], currency: Currency | str | None = None
    ) -> Self:
        """Their sum — `currency` says what none of them add up to; `CurrencyMismatch` when
        they are of several (`totals` adds each)."""
        items = list(amounts)
        if not items:
            if currency is None:
                raise ValueError("a total of no amounts needs its currency")
            return cls.zero(currency)
        added = cls.zero(currency if currency is not None else items[0].currency)
        for one in items:
            added = added + one
        return added

    @classmethod
    def totals(cls, amounts: Iterable["Money"]) -> dict[Currency, Self]:
        """Their sum by currency — what a report of several currencies shows."""
        added: dict[Currency, Self] = {}
        for one in amounts:
            added[one.currency] = added.get(one.currency, cls.zero(one.currency)) + one
        return added

    @classmethod
    def average(cls, amounts: Iterable["Money"]) -> Self:
        """Their mean, exact — of one currency."""
        items = list(amounts)
        if not items:
            raise ValueError("an average of no amounts has none")
        return cls.total(items) / len(items)
