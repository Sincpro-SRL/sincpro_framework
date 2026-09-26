# PRD_09: Analytics — declared frames, snapshots, one answer per view

- **Status**: proposal
- **Depends on**: nothing (PRD_08 schedules refreshes)
- **Research**: scratchpad `12_analytics_datasets.md` (probes: `probe_analytics.py`,
  `probe_attach.py`)

## Problem

A screen that shows a pivot, a chart or a KPI tile asks the database again for every cell,
every filter change and every user, and the answer travels as rows of JSON the client adds up
itself. Measured on this framework:

| | Today |
|---|---|
| `export` of 20 000 rows with defaults | **800 statements** (pages of 50, a count on each) — 4 with the right pagination |
| 20 000 rows | 2.12 MB as JSON · **94 KB as Parquet** (~22× smaller) |
| `pivot` | 4 statements every time (cells, row margin, column margin, total) |
| `Decimal` in an answer | a string, and nothing says so; `avg` a float |
| `pivot` / `group_by_levels` / `measures` over an entrypoint | not exposed at all |

The pieces are good — `Criteria` already has `Measure`, `Level` with grains, `Grouping`, and
`Meta` describes every field — but there is no declared "this is the sales analysis", no
snapshot to answer many views from, and no compact shape for a frontend.

## Goals

1. Declare an analysis **once** — source, joins, dimensions, metrics — in the framework's
   vocabulary.
2. Answer a pivot, a chart or a tile in **one call**: the data and the view it is for, typed.
3. Answer many views from **one snapshot**, not one query per cell.
4. Merge data from several aggregates and bounded contexts.
5. Heavy engines optional: without DuckDB or pyarrow it still works, on the live database.

## Non-goals

- A BI tool. The frontend renders; the framework answers.
- Arbitrary SQL from the client — a view is expressed with `Criteria` and the declaration.

## Prior art

| | Idea | What we take |
|---|---|---|
| Cube, dbt MetricFlow, Malloy, Superset datasets | Declare entity, dimensions (with grain), measures, joins once; answer any view from it | The declaration |
| Evidence.dev | Parquet snapshots + DuckDB | Snapshots as the unit of reuse |
| Mosaic (UW) | Each view is an aggregate query; the server returns only aggregates | Views answered as aggregates, never raw rows by default |
| ECharts `dataset` | A table plus an encoding spec | "Data + the view it is for" in one response |
| DuckDB | In-process OLAP; `ATTACH` Postgres/SQLite; `GROUPING SETS` / `ROLLUP`; Parquet | The engine for snapshots and pivots |
| Apache Arrow | Columnar interchange, IPC over HTTP | The binary answer, for clients that want it |

## The model

```python
sales = Frame(
    "sales_by_region",
    source=InvoiceLine,
    where=Criteria.model_validate({"where": {"field": "state", "value": "posted"}}),
    joins=(
        Join("account", Account, on="account_id"),
        Join("partner", Partner, on="partner_id", context="crm"),       # another context
    ),
    dimensions=(
        Dimension("journal_id"),
        Dimension("posted_at", grains=("day", "month", "year")),
        Dimension("account.type", label={"default": "Account type", "es": "Tipo de cuenta"}),
    ),
    metrics={
        "debit": Metric(Measure(function="sum", field="debit"), format="currency"),
        "lines": Metric(Measure(function="count", field="id")),
        "balance": Metric(expression=("debit", "-", "credit")),        # computed after the fold
    },
    refresh=Refresh(watermark="updated_at", every=Cron("*/15 * * * *", timezone="UTC")),
)
billing.frame(sales)
```

- Everything in it is vocabulary the framework already has: `Criteria`, `Measure`, grains, `Meta`
  labels. A `Frame` is registered on a bus like a Feature and appears in its catalog.
- `refresh` is a schedule (PRD_08).

## Answering a view

```python
answer = billing.frames.answer(
    "sales_by_region",
    PivotView(rows=("journal_id",), columns=(Level(field="posted_at", grain="month"),),
              values=("debit", "balance"), totals="both"),
    criteria,                                   # the user's filter, as always
)
```

The view kinds are closed: `PivotView`, `SeriesView` (charts: bar, line, area, scatter, pie,
heatmap), `TileView` (one metric), `TableView` (rows, paged).

**How it is answered**, from the cheapest available:

| Engine | When | How |
|---|---|---|
| Snapshot (DuckDB) | the `[analysis]` extra is installed and a snapshot exists | one `GROUPING SETS` statement over the Parquet snapshot — 5 ms for a `ROLLUP` over 200 000 rows |
| Live | otherwise | the repository's own `pivot` / `group_by_levels` / `measures`, on the database |

The caller cannot tell which answered, except by `snapshot` in the response.

## Snapshots

- **Materialized** by compiling the same `SELECT` the repository would run — including its
  `narrowed()` scope, so a snapshot never shows what the repository would not — and writing it to
  Parquet through DuckDB (`ATTACH` the database, `COPY … TO parquet`: 2.6 s for 200 000 rows).
- **Identified** by a hash of the definition, the watermark and the scope. That id is the ETag.
- **Refreshed** by the schedule, or on demand with a Command (`CommandRefreshFrame`), through the bus.
- **Merged**: joins across aggregates and contexts are joins between snapshots inside DuckDB.

## What the frontend receives

One JSON envelope for any view:

```json
{
  "frame": "sales_by_region",
  "snapshot": {"id": "3f9a…", "etag": "\"3f9a…\"", "as_of": "2026-09-26T14:00:00Z", "fresh": true},
  "definition": {
    "dimensions": {"posted_at": {"type": "datetime", "label": {"default": "Posted"}, "grains": ["day", "month", "year"]}},
    "metrics": {"debit": {"type": "decimal", "scale": 2, "format": "currency"}, "balance": {"type": "decimal", "derived": true}}
  },
  "view": {"kind": "pivot", "rows": ["journal_id"], "columns": ["posted_at:month"], "values": ["debit", "balance"], "totals": "both"},
  "data": {
    "encoding": "columns",
    "columns": ["journal_id", "posted_at:month", "debit", "balance", "_grouping"],
    "types": ["text", "date", "decimal(18,2)", "decimal(18,2)", "int"],
    "values": [["BNK", "BNK", null], ["2026-01", "2026-02", null], ["1200.00", "980.50", "2180.50"], ["…"], [0, 0, 3]]
  },
  "drill": {"criteria_template": {"where": {"all": [{"field": "journal_id", "value": "{journal_id}"}]}}},
  "dropped": []
}
```

- **Columnar**, even in JSON: 94 KB-class payloads instead of MB.
- **`_grouping`** marks each row as a cell, a margin or the total — the client never adds numbers.
- **Decimals are strings typed `decimal(p,s)`**, never floats.
- **One drill template** instead of a criteria per bucket.
- `Accept: application/vnd.apache.arrow.stream` returns the same answer as Arrow IPC (the `[arrow]`
  extra); `GET /frames/{name}/snapshots/{id}.parquet` serves the raw snapshot with its ETag and
  `Cache-Control: immutable`, for web clients that query it locally (DuckDB-WASM, Perspective).

The server-side answer is the default because the client is shared between mobile and web, and
DuckDB-WASM does not run in React Native.

## Entrypoints

| | |
|---|---|
| JSON-RPC, MCP | `frame.list`, `frame.describe`, `frame.answer`, `frame.refresh` (a Command on the bus); MCP answers carry a cell cap and `truncated` |
| HTTP | the Arrow and Parquet bodies, with `304 Not Modified` |
| gRPC | `bytes arrow_ipc` in the answer |

## What the design system needs

A client adapter (in the style of `@sincpro/criteria`) that decodes the envelope — JSON or Arrow —
into shapes the design system already speaks: a `Frame` of `{columns, rows, value(row, col)}`, a
`PivotTable` driven by `_grouping` and the drill template, a `Chart` from the closed view kinds, a
`MetricTile`, currency/scale/locale formatters and a "as of … · refresh" indicator. The design
system never imports Arrow, DuckDB or Perspective.

## Phases

1. **No new dependencies**: `pivot` / `export` / `explain` on the `Repository` port and in
   `MemoryRepository`; `export` defaults that stream in large batches (4 statements, not 800);
   Queries on the entrypoints for grouping and pivot; the columnar JSON encoder; `scale` / `format`
   on `FieldMeta`.
2. `Frame`, `Dimension`, `Metric`, the view kinds — answered live.
3. The DuckDB snapshot engine, refresh schedules, cross-context joins.
4. The Arrow IPC and Parquet routes.
5. The optional web mode (DuckDB-WASM / Perspective over the Parquet snapshot).

## Open questions

- `Frame` or `Dataset`? `Dataset` is already the example aggregate in the docs and
  `sincpro_synthesis`'s catalog aggregate; `Frame` is the design system's word for a rectangle of
  values.
- One snapshot per scope (tenant), or one snapshot filtered at answer time?
- Where are snapshots stored, and who evicts old ones?
- Are translated fields and `after_read` hooks materialized resolved, or raw?
