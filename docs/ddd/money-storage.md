# Storing money: one value, three ways to keep it (design)

> Status: **design, step 1 built.** `MultiCurrency` and the JSON storage are built and run in
> [money.md](money.md) ("Several currencies at once", "In an Entity"); steps 2–4 below are the design
> still to build, and this page goes when they are.

An Entity's field is `total: Money` — or `total: MultiCurrency`, an amount with its equivalents in other
currencies. How the table keeps it is a choice of the table, never of the domain: the same field
maps to a JSON column, to columns, or to both, and the Feature reads `invoice.total` as a `Money`
in every case.

## What the evidence says

| Finding | Source |
|---|---|
| Postgres keeps no statistics on the values **inside** a `jsonb`: the planner guesses (a fixed ~0.1% before PG 13), and a query filtered by a JSON field can choose a bad plan — one benchmark ran ~2000× slower than the same column | [Heap — When to avoid JSONB](https://heap.io/blog/when-to-avoid-jsonb-in-a-postgresql-schema), [pganalyze — JSONB selectivity](https://pganalyze.com/blog/5mins-postgres-planner-jsonb-selectivity) |
| PG 13 estimates containment from MCVs and histograms; PG 14 adds `CREATE STATISTICS` on an expression — the gap narrows, it does not close | [pganalyze](https://pganalyze.com/blog/5mins-postgres-planner-jsonb-selectivity) |
| A B-tree does not index a `jsonb` column: an **expression index** on the extracted, cast value does — and the query's cast must match the index's exactly | [Stanza — expression indexes](https://www.stanza.dev/courses/postgresql-json/json-indexing/postgresql-json-expression-indexes) |
| A **STORED generated column** computed from the `jsonb` (`(data->>'price')::numeric`) is a real column — indexed, with statistics, always in sync; write `STORED` explicitly (PG 18 makes `VIRTUAL` the default) | [Stanza — generated columns from JSON](https://www.stanza.dev/courses/postgresql-json/json-patterns/generated-columns-json), [Cybrosys — virtual columns in PG 18](https://www.cybrosys.com/research-and-development/postgres/a-complete-guide-to-virtual-generated-columns-in-postgresql-18) |
| A JSON number is a float to most readers: an amount travels as a **string** | [ex_money_sql](https://readme.hex.pm/ex_money_sql/1.3.0), [MongoDB — monetary data](https://mongodb.com/docs/v8.0/tutorial/model-monetary-data) |
| Never Postgres's `money` type (its decimals and symbol come from `lc_monetary`, one per database): `numeric` plus a currency code | [Crunchy Data — Working with money in Postgres](https://www.crunchydata.com/blog/working-with-money-in-postgres) |
| JSON adds a field with no migration; changing a shape still needs one | [AppMaster](https://appmaster.io/ko/blog/postgresql-jsonb-vs-jeonggyuhwadoen-teibeul-seontaeg-maigeureisyeon), [Truewind](https://engineering.truewind.ai/i-ditched-schemas-for-postgresqls-jsonb-in-our-document-parser-heres-why) |
| SAP's Universal Journal (ACDOCA) carries **up to 10 parallel currencies per line as columns** (HSL, KSL, OSL, …) — reporting in N currencies is real, and done with columns | [SAPinsider — financial data model](https://sapinsider.org/wp-content/uploads/2023/03/DixonCairncross_KeyFinancialDataModelConsiderations_Vegas2023.pdf), [SAP Learning — currency types](https://learning.sap.com/courses/maintaining-financial-accounting-core-settings/managing-currency-types) |
| Ledgers keep a balance **per currency** and never normalize it: a total in another currency is a valuation at a date | [TigerBeetle — currency exchange](https://docs.tigerbeetle.com/coding/recipes/currency-exchange/) |
| SQLAlchemy maps one attribute onto several columns with `composite()`, typed from a dataclass, positional constructor and `__composite_values__` | [SQLAlchemy 2.0 — Composite column types](https://docs.sqlalchemy.org/en/20/orm/composites.html) |

So: JSON for what is flexible and read whole, columns for what is filtered, summed and indexed —
and the two together when a table needs both, the columns derived by the database from the JSON.

## The values

`Money` stays what it is. `MultiCurrency` joins it: what happened, and its equivalents — each with the
rate it was converted by, frozen when it was recorded.

```
total = MultiCurrency(Money("100", "USD")).at(usd_to_bob, usd_to_eur)
total.origin          Money('100', 'USD')                 what happened, never changed
total["BOB"]          Money('696.00', 'BOB')              like a translated text's total["es_BO"]
total.rate("BOB")     ExchangeRate('USD', 'BOB', '6.96', on=2026-10-01, source='BCB')
total.at(new_rate)    a new MultiCurrency: another equivalent, or one re-valued at another date
```

Stored, it is one document:

```json
{"origin": {"amount": "100", "currency": "USD"},
 "in": {"BOB": {"amount": "696.00", "rate": "6.96", "on": "2026-10-01", "source": "BCB"},
        "EUR": {"amount": "92.10", "rate": "0.921", "on": "2026-10-01", "source": "ECB"}}}
```

## The use cases, and the way for each

A framework does not pick the case; it studies them and gives each its way. Every row is built from
the same pieces — `Money`, `MultiCurrency`, `ExchangeRate`, `ExchangeRates` — and one of the three
storages.

| Case | Who has it | The value | Kept as | What it uses |
|---|---|---|---|---|
| **A document in one currency** — a quote, a purchase order, an invoice in USD | every ERP | `total: Money` | JSON | `+`, `sum`, `round`, taxes by line (`price * qty * Decimal("0.13")`, `round` per line) |
| **The same line in the company's and the group's currency** — what accounting posts | Odoo (2), SAP (up to 10) | `total: MultiCurrency` — origin and equivalents, each rate frozen at posting | JSON + a generated column per currency the ledger sums | `MultiCurrency(m).at(rate_company, rate_group)`; the ledger summed on `total_bob`, `total_usd` |
| **A price list in several currencies** — 10 USD and 70 BOB, each set by hand, not converted | retail, SaaS | `price: MultiCurrency` with equivalents of no rate (`source="manual"`) | JSON | `price["BOB"]` for the customer's currency; nothing converted |
| **A bank account, a wallet, a cash box** — one currency each, its balance moved by movements | core banking, crypto wallets, POS | `balance: Money` on an account of one currency — never several in one | Columns: `NUMERIC` + currency, summed and indexed | `+`, `-`, `is_negative`; a total of accounts is `Money.totals(...)`, per currency |
| **An exchange** — 100 USD sold for BOB, with a fee | exchange houses, Wise, banks | two movements (one per account and currency), the `ExchangeRate` stored with them, the fee a `Money` of its own | the movements as Columns, the rate as JSON beside them | `Money("100", "USD").convert(rate)`; the spread is the difference with the official rate |
| **A position valued in one currency at a date** — a portfolio, a treasury dashboard, "how much do we have in BOB today" | fintech, treasury | computed, never stored as truth — or a snapshot `MultiCurrency` kept for history | — (or JSON for the snapshot) | `ExchangeRates.on(...)` for each currency, `convert`, `Money.total` |
| **A month-end revaluation** — unrealized exchange gain or loss | accounting | the posted `MultiCurrency` beside one at the closing rate | JSON | `posted.at(closing_rate)`; the difference of the two equivalents is the entry the ERP writes |
| **A report as a matrix** — sales by customer in BOB, USD and EUR | management, consolidation | `total: MultiCurrency` | JSON + generated columns | Criteria `measures` + `group_by` on `total.BOB`, `total.USD`; a DataFrame with a column per currency |
| **A crypto amount, and its cost for taxes** — 0.5 ETH bought when it was worth 1,200 USD | exchanges, wallets, tax tools | `Money(..., Currency("ETH", 18))`; the cost a `MultiCurrency` with origin ETH and USD at the trade's rate | JSON (any precision, nothing to migrate) | sixty digits; the cost basis read from the frozen equivalent, never re-priced |
| **Installments, splits, prorating** — a 100 BOB plan in 3 | lending, subscriptions, cost allocation | `Money` | as the document | `allocate(1, 1, 1)`, `split(n)`: no cent lost |

What none of them does: add two currencies, store a float, or re-compute a posted conversion with
another day's rate. Each storage is a column choice of the table; the Entity and the Criteria read
the same paths in all of them.

## Three ways to keep it

| | **JSON** — the default | **Columns** | **JSON + generated columns** |
|---|---|---|---|
| Declared | `Column("total", TypedJson(Money))` — exists today | `total_amount NUMERIC`, `total_currency VARCHAR(8)` (or one currency column shared, as Odoo's `currency_field`) mapped as one `Money` (`composite`) | the JSON column, plus `total_bob NUMERIC GENERATED ALWAYS AS ((total->'in'->'BOB'->>'amount')::numeric) STORED` per currency reported |
| N currencies, a crypto's 18 decimals | any, nothing to migrate | a column per currency and a `NUMERIC(p, s)` chosen | any in the JSON; a column for each one reported |
| Amount and currency apart | never | possible, unless a check says otherwise | never: the database derives the columns |
| Filter, order, sum in SQL | by extraction and a cast; the planner guesses | native, with statistics | native on the generated columns |
| Index | an expression index, the cast matched | a plain index | a plain index on the generated column |
| Add a reporting currency | nothing | a migration and a backfill | a migration adding one generated column — the database fills it |
| For | documents, quotes, what is read whole | a ledger's hot paths, a table of millions summed by amount | multi-currency reporting at scale: the matrix in BOB, USD and EUR |

The Entity is the same in the three: `total: Money` or `total: MultiCurrency`. The Criteria reads the
same paths in the three — `total.amount`, `total.currency`, `total.BOB` — and the repository turns
each into the extraction, the column or the generated column the table has.

## What it takes

1. **`MultiCurrency`** — the value, its JSON, `at(rate, ...)`, `[code]`, `rate(code)`, `origin`; stored
   with `TypedJson` as `Money` is. And `Money`'s amount written in fixed point (`"0.000000000000000001"`,
   never `"1E-18"`).
2. **Columns** — `Money` gains `__composite_values__` (its constructor is already positional), so
   `map_aggregates(..., properties={Invoice: {"total": composite(Money, table.c.total_amount,
   table.c.total_currency)}})` maps it; a declaration that writes the two columns, and the shared
   currency column for several amounts.
3. **Generated columns** — a declaration of the currencies a JSON field reports in, written as
   `Computed(..., persisted=True)` columns, and the migrations' autogenerate writing them.
4. **The Criteria and `measures` over each** — the same path to the extraction, the column or the
   generated column; and the DataFrame expanding a `Money` into amount and currency, a `MultiCurrency`
   into a column per currency.

## Open questions

1. **The order**: 1 → 4 above, one step at a time, each with its runnable section?
2. **The default storage**: JSON (proposed) — columns and generated columns where a table needs them?
3. **Which currencies a `MultiCurrency` is filled with**: always explicit (`at(rates)`, proposed), or
   also declared on the Entity and filled from `ExchangeRates` on save, as an optional hook?
