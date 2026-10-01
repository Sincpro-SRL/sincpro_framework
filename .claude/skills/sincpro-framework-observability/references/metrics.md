# Metrics

The long form, in the framework repo: `docs/observability/metrics.md`.

## By itself

Nothing to declare. Each run lands on `sincpro.use_case.duration` (seconds), labelled:

`service.name`, `sincpro.context`, `sincpro.use_case`, `sincpro.layer`, `sincpro.outcome`
(`ok` / `expected` / the failure's kind), `error.type`.

From that one metric a dashboard has rate (count), errors (outcome) and latency (buckets) of every
use case. A failure answered by an error handler is still recorded as its kind — the caller got an
answer, the use case did not succeed.

Framework pieces that count themselves: `sincpro.cache.outcomes`, `sincpro.idempotency.outcomes`,
`sincpro.queue.deliveries`.

## Declared: decorate

```python
from sincpro_framework.observability.metrics import metrics, of

@billing.feature(CommandIssueInvoice)
@metrics.counts(by=of(CommandIssueInvoice).currency)
@metrics.sums(of(ResponseIssueInvoice).total, by=of(CommandIssueInvoice).currency, unit="BOB")
@metrics.measures(of(ResponseIssueInvoice).lines, buckets=(1, 5, 10, 50))
class IssueInvoice(Feature): ...
```

| Decorator | Records on each **successful** run | Named |
|---|---|---|
| `@metrics.counts(by=)` | one more | `{context}.{use_case}.runs` |
| `@metrics.sums(of(X).field, by=, unit=)` | the field's value, added (a counter; a negative is dropped) | `{context}.{use_case}.{field}` |
| `@metrics.measures(of(X).field, by=, unit=, buckets=)` | the field's value, as a distribution | `{context}.{use_case}.{field}` |

`X` is the use case's Command, its Response, or its context type (`of(BillingContext)["channel"]`,
read off the execution context) — a path into any other DTO or context is refused at import. A
failed run is not counted here (it is in `sincpro.use_case.duration`, by kind).

**Labels are the project's choice.** One that is not an `Enum`, a `Literal` or a `bool` is accepted
with one warning where declared (`customer_id is str`): each distinct value is one more series.

**Every series also carries** `release`, `service.name`, `service.version` and the execution's
`tenant`, plus the context keys the bus names before the build
(`UseFramework(..., metric_labels=["company"])` or `metric_labels:` in the settings).

## By hand, inside `execute`

```python
@pricing.feature(CommandPriceOrder)
class PriceOrder(Feature):
    lookups = metrics.counter(by=of(CommandPriceOrder).channel)   # pricing.price_order.lookups
    pricing = metrics.timer()
    discount = metrics.histogram(unit="BOB", buckets=(0, 10, 100))
    in_flight = metrics.up_down()

    def execute(self, dto: CommandPriceOrder) -> ResponsePriceOrder:
        self.in_flight.add(1)
        try:
            with self.pricing.time():          # seconds, with the block's outcome
                self.lookups.add(2, dto)        # labels read off the sources handed in
                self.discount.record(7)
            return ResponsePriceOrder(total=Decimal(93))
        finally:
            self.in_flight.add(-1)
```

| Instrument | Records | Call |
|---|---|---|
| `metrics.counter(by=, unit=)` | only up | `self.x.add(n, *sources)` |
| `metrics.up_down(by=, unit=)` | up and down | `self.x.add(±n, *sources)` |
| `metrics.histogram(by=, unit=, buckets=)` | a distribution | `self.x.record(value, *sources)` |
| `metrics.timer(by=, buckets=)` | seconds with `sincpro.outcome` | `with self.x.time(*sources):` |

`sources` are the objects `by=` references read from (the Command, the Response, a DTO built midway).

## Backend

| `OTEL_METRICS_EXPORTER` | alias `SINCPRO_METRICS_BACKEND` | Recorder |
|---|---|---|
| not set | `auto` | OpenTelemetry when a meter provider/OTLP endpoint exists, else nothing |
| `otlp` | `otel` | `OtelRecorder` (`[opentelemetry]`) |
| `prometheus` | `prometheus` | `PrometheusRecorder`, scraped at `/metrics` (`[prometheus]`) |
| `none` / `OTEL_SDK_DISABLED=true` | `off` | nothing |

`SINCPRO_METRICS_BACKEND` set to anything but `auto` wins. `metrics.use(recorder)` in code;
`metrics.using(recorder)` for a block (tests use `InMemoryRecorder`).

Prometheus: names become `context_use_case_field`, a counter ends `_total`; `FastApiGateway.app()`
serves `/metrics` by itself when the process records to Prometheus. With several worker processes
set `PROMETHEUS_MULTIPROC_DIR` before start.

A backend of your own implements `Recorder` (`add`, `record`) — `from
sincpro_framework.observability.metrics import Recorder` — and proves itself with `RecorderContract`
(`sincpro_framework.observability.metrics.testing`). `InMemoryRecorder` answers `names()`,
`totals(name)` and `observations(name)` for assertions. A recorder never has to guard itself: every call is shielded, measuring never
fails the use case it measures.
