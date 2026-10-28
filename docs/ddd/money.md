# Money: an amount in its currency

An amount never travels without its currency, never rounds by accident, and never adds to another
currency. `Money` is a value object — a frozen DTO — that behaves as Python expects a number to
behave: it adds, sums, compares, hashes, rounds and formats with the operators and built-ins
Python already has, takes the floats and ints an ERP or a spreadsheet holds, and refuses what has
no meaning (BOB + USD).

Every block on this page runs, in order, in `tests/docs/test_persistence_guide.py`.

## Like a number

```python
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal

from sincpro_framework.ddd import Currency, CurrencyMismatch, ExchangeRate, Money

price = Money("10.50", "BOB")
lines = [price * 3, Money("0.75", "BOB")]

total = sum(lines)                                    # Money('32.25', 'BOB')
assert total == Money("32.250", "BOB")                # equal by value
assert {total, Money("32.25", "BOB")} == {total}      # hashable: a dict key, a set member
assert max(lines) == price * 3 and total > price      # ordered within one currency

iva = total * Decimal("0.13")                         # Money('4.1925', 'BOB'): exact
assert round(iva) == Money("4.19", "BOB")             # to the currency's decimals
assert round(iva, 3) == Money("4.193", "BOB")
assert f"{iva:.2f}" == "4.19 BOB" and str(total) == "32.25 BOB"

assert total / 4 == Money("8.0625", "BOB")            # by a number: Money
assert round(price / total, 4) == Decimal("0.3256")   # by Money: its share, a Decimal
assert -price < 0 and abs(-price) == price
assert not Money("0.001", "BOB")                      # zero at BOB's two decimals
```

**A bare number is an amount of the money's own currency** — what a float from Odoo, an int from
a form or a Decimal from a calculation is next to a money:

```python
assert Money(0.1, "BOB") + 0.2 == Money("0.3", "BOB")   # by the digits it prints: never 0.30000000000000004
assert price + 100 == Money("110.50", "BOB") and 20 - price == Money("9.50", "BOB")
assert price * 1.13 == Money("11.865", "BOB") and price == 10.5 and price > 0
assert float(iva) == 4.1925                               # out, for a chart or a library

try:
    price + Money("1", "USD")
except CurrencyMismatch:
    pass                                                  # BOB + USD is never a number
assert price != Money("10.50", "USD")                     # another value, never an error
```

`price + "1"`, `price * price` and `3 / price` are `TypeError`: a text, a money squared and a
number divided by a money mean nothing.

## Rounding, splitting, converting

```python
assert Money("0.125", "BOB").rounded(ROUND_HALF_EVEN) == Money("0.12", "BOB")   # a mode of `decimal`
assert Money("10.03", "BOB").rounded(to="0.05") == Money("10.05", "BOB")        # cash rounding
assert round(Money("2.5", "JPY")) == Money("3", "JPY")                          # JPY: no decimals

assert Money("100", "BOB").allocate(1, 1, 1) == [
    Money("33.34", "BOB"), Money("33.33", "BOB"), Money("33.33", "BOB")
]                                                                               # no cent lost, none invented
assert Money("100", "BOB").allocate(70, 30) == [Money("70", "BOB"), Money("30", "BOB")]
assert sum(Money("100", "BOB").split(3)) == Money("100", "BOB")

usd_to_bob = ExchangeRate("USD", "BOB", "6.96", on=date(2026, 10, 27), source="BCB")
assert Money("100", "USD").convert(usd_to_bob) == Money("696.00", "BOB")
assert Money("696", "BOB").convert(usd_to_bob) == Money("100", "USD")           # either way
assert Money("1", "USD").convert(usd_to_bob, rounded=False).amount == Decimal("6.96")
```

## Over many — a report

```python
amounts = [Money("10", "BOB"), Money("5", "USD"), Money("20", "BOB")]

assert Money.totals(amounts) == {"BOB": Money("30", "BOB"), "USD": Money("5", "USD")}
assert Money.total([], "BOB") == Money.zero("BOB")
assert Money.average([Money("10", "BOB"), Money("20", "BOB")]) == Money("15", "BOB")
assert sorted([Money("3", "BOB"), Money("1", "BOB")])[0] == 1
assert Money.parse("BOB 10.50") == Money.parse("10.50 BOB") == price
```

## A currency of your own

A crypto, points, a bank's unit of account: a `Currency` given its decimals. It is a `str` — its
code — so it compares, labels and serializes as one.

```python
BTC = Currency("BTC", 8)
ETH = Currency("ETH", 18)

wei = Money("0.000000000000000001", ETH)
assert wei.is_positive and wei.currency.minor_units == 18
assert round(Money("0.123456789", BTC)) == Money("0.12345679", BTC)
big = Money("123456789012.123456789012345678", ETH)
assert (big * 3).amount == Decimal("370370367036.370370367037037034")   # sixty digits kept
assert Currency("BOB") == "BOB" and Currency("BOB").minor_units == 2      # ISO 4217's, by code
```

A money read from JSON resolves its code among `Money.currencies` — ISO 4217. A subclass says the
codes of its own, and what else it changes:

```python
from decimal import ROUND_HALF_EVEN

from sincpro_framework.ddd.money import ISO_CURRENCIES


class Crypto(Money):
    currencies = {**ISO_CURRENCIES, "BTC": BTC, "ETH": ETH}
    rounding = ROUND_HALF_EVEN


sent = Money("0.5", BTC).model_dump(mode="json")
assert sent == {"amount": "0.5", "currency": "BTC"}
assert Crypto.model_validate(sent).currency is BTC
```

## Its API

**Python's own protocols:**

| Protocol | What it does |
|---|---|
| `==`, `hash()` | by value: `10.5 BOB == 10.50 BOB`; another currency is another value (`False`); a bare number is compared as an amount |
| `<` `<=` `>` `>=`, `min`, `max`, `sorted` | within one currency, or against a bare number; across two, `CurrencyMismatch` |
| `+` `-`, `sum()` | with a money of the same currency or a bare number, either side; `sum(items)` starts at `0` |
| `*` | by a number, either side |
| `/` | by a number → `Money`; by a money of the same currency → its share, a `Decimal` |
| `-m` `+m` `abs(m)` | sign |
| `bool(m)` | `False` when it is zero at the currency's decimals |
| `round(m)` · `round(m, n)` | to the currency's decimals, or to `n`, by the money's `rounding` |
| `float(m)` | the amount as a float — for a chart, a model, a library; never stored back |
| `str(m)` · `repr(m)` · `f"{m:,.2f}"` | `"32.25 BOB"` · `Money('32.25', 'BOB')` · the amount formatted (rounded by the money's rule), its code after |

**Its own:**

| Member | What it does |
|---|---|
| `Money(amount, currency)` | `amount` a `str`, `int`, `float` or `Decimal`; `currency` a `Currency` or its code |
| `Money.zero(c)` · `Money.parse(text)` | the start of a sum; one written as `"10.50 BOB"` or `"BOB 10.50"` |
| `m.rounded(mode=None, to=None)` | to the currency's decimals by a mode of `decimal`, or to a step |
| `m.allocate(*ratios)` · `m.split(n)` | parts that add up to it exactly, the remainder's units to the first ones |
| `m.convert(rate, rounded=True)` | into the rate's other currency, either way; `CurrencyMismatch` for a rate of neither |
| `m.is_zero` · `m.is_negative` · `m.is_positive` | at the currency's decimals, as an accountant reads them |
| `Money.total(items, c)` · `Money.totals(items)` · `Money.average(items)` | the sum of one currency, the sums by currency, the mean |
| `Money.currencies` · `rounding` · `context` | what a subclass changes: the codes it reads, how it rounds (`ROUND_HALF_UP`), the digits it keeps (60) |

**`ExchangeRate(base, quote, rate, on=None, source=None)`** — what one `base` is worth in `quote`,
on a date, by a source; `rate.inverse()` the other way. Stored beside the transaction that used
it, a conversion is never computed again with another day's rate.

**JSON** is `{"amount": "10.50", "currency": "BOB"}` — the amount a string, so no client and no
broker reads it as a float; a JSON number is taken on the way in, by its digits.

## Rates by date

`ExchangeRates` is the port an ERP fills — its table, its daily load, the source it trusts, per
company if it has several. `on(base, quote, day)` answers the latest rate on or before the day,
either way it was stated, or `None`:

```python
from sincpro_framework.ddd import InMemoryExchangeRates

rates = InMemoryExchangeRates(
    [
        ExchangeRate("USD", "BOB", "6.96", on=date(2026, 10, 1), source="BCB"),
        ExchangeRate("USD", "BOB", "6.97", on=date(2026, 10, 20), source="BCB"),
    ]
)
assert rates.on("USD", "BOB", date(2026, 10, 19)).rate == Decimal("6.96")
assert rates.on("BOB", "USD", date(2026, 10, 27)).base == "BOB"            # the inverse
assert rates.on("USD", "EUR", date(2026, 10, 27)) is None                  # nobody said
```

An adapter of a project's own proves itself with `ExchangeRatesContract`
(`sincpro_framework.runtime.testing`).

## In a metric

`@metrics.sums(field_path(ResponseIssueInvoice).total)` over a `Money` field records its amount
labelled by its currency, with nothing declared — one series per currency, never summed across
(`labelled_by` on the class says so). `measures` does the same.

## Several currencies at once: `MultiCurrency`

An amount as it happened and what it is worth in other currencies — the company's, the group's,
the reports' — each equivalent with the rate it was converted by, frozen when it was recorded. It
is read as a text kept in several languages is read: by its key.

```python
from sincpro_framework.ddd import MultiCurrency

eur_to_usd = ExchangeRate("EUR", "USD", "1.0858", on=date(2026, 10, 1), source="ECB")
paid = MultiCurrency(Money("100", "USD")).at(usd_to_bob, eur_to_usd)

assert (paid.origin, paid["BOB"], paid["EUR"]) == (
    Money("100", "USD"), Money("696.00", "BOB"), Money("92.10", "EUR")
)
assert paid.codes == ("USD", "BOB", "EUR") and "BOB" in paid
assert paid.rate("EUR") == eur_to_usd                       # kept as published, never inverted

price = MultiCurrency(Money("10", "USD")).at(Money("70", "BOB"))   # a price set by hand: no rate
assert price["BOB"] == Money("70", "BOB") and price.rate("BOB") is None

closing = paid.at(ExchangeRate("USD", "BOB", "6.97", on=date(2026, 10, 31)))
assert closing["BOB"] - paid["BOB"] == Money("1.00", "BOB")   # the exchange difference, paid untouched

lines = [paid, paid]
assert sum(lines)["BOB"] == Money("1392.00", "BOB")          # a document's lines, currency by currency
assert sum(lines).rate("BOB") == usd_to_bob                   # a rate every line shares is kept
```

- **`at(*conversions)`** answers a new value: a rate converts the origin either way it is stated,
  a `Money` is an amount set by hand; an equivalent already held is replaced, the origin never.
- **A rate is kept as it was stated**, with the currency it is stated for (`base`, ISO 20022's unit
  currency) — the digits the bank published, never their inverse.
- **Adding** two needs the same currencies; an equivalent keeps its rate only when both share it.
- JSON is one document: `{"origin": {...}, "equivalents": {"BOB": {"amount": "696.00", "rate":
  "6.96", "base": "USD", "on": "2026-10-01", "source": "BCB"}, ...}}`. A subclass reads codes of
  its own, as `Money`'s does.

## In an Entity

A field is `total: Money` or `total: MultiCurrency`; the table keeps it in one JSON column —
`jsonb` on Postgres — and the repository reads it back as the value, with all its API:

```python
from sqlalchemy import Column, Integer, MetaData, Numeric, Table, cast, create_engine, func, insert, select

from sincpro_framework.data_layer.orm.sqlalchemy.domain.custom_fields import TypedJson

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
    connection.execute(insert(invoices), [{"id": 1, "total": paid}, {"id": 2, "total": paid}])
    assert connection.execute(select(invoices.c.total)).scalars().first() == paid
    in_bob = cast(invoices.c.total["equivalents"]["BOB"]["amount"].as_string(), Numeric(38, 18))
    assert connection.execute(select(func.sum(in_bob))).scalar() == 1392   # summed by the database
```

In an Entity's table that is `entity_table("invoice", metadata, Column("total", TypedJson(Money)))`
— nothing else to declare: any precision, any currency, nothing to migrate when one more is held.
The database sums it by extracting the amount; when a table of millions is summed and filtered by
currency, columns derived from the JSON do it natively ([money-storage.md](money-storage.md)).

## Not here

- **Columns** — `Money` on two columns, several amounts sharing one currency column, columns
  generated from the JSON for the currencies a report sums — and the Criteria over each: designed
  in [money-storage.md](money-storage.md), the next steps.
- Formatting for a locale (the frontend's, `Intl.NumberFormat`), amounts in words, exchange
  differences and revaluation entries — the ERP's rules.
