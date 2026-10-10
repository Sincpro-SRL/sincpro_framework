# Metrics

Every use case of every bounded context is measured by itself: how often it runs, how often it
fails and how long it takes. A use case that wants more **decorates** itself — it counts, sums or
measures a field — and one that needs something elaborate declares an instrument on its class
and records it inside `execute`. Nothing is named by a string: names come from the bounded
context, the use case and the field or attribute, and labels are field references checked when
they are written.

The core records nothing and needs no extra. `[prometheus]` (Sincpro's reference stack) or
`[opentelemetry]` bring a backend; OpenTelemetry's own `OTEL_METRICS_EXPORTER` picks it, as it does
for the traces (`SINCPRO_METRICS_BACKEND` is kept as an alias). The design and the why are
[PRD 03 §4](../prd/PRD_03_observability-tracing.md#4-metrics).

Every block on this page runs, in order, in `tests/docs/test_persistence_guide.py`.

## The context

```python
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.observability.metrics import InMemoryRecorder, metrics, of


class Currency(StrEnum):
    BOB = "BOB"
    USD = "USD"


class CommandIssueInvoice(DataTransferObject):
    customer_id: str
    currency: Currency
    total: Decimal
    channel: Literal["web", "pos"] = "web"


class ResponseIssueInvoice(DataTransferObject):
    number: str
    total: Decimal
    lines: int


billing = UseFramework("billing", log_after_execution=False)
```

## Measured by itself: every use case

Nothing to declare. Each run lands on one histogram, `sincpro.use_case.duration` (seconds),
labelled with the service behind it (`service.name`: the library or service, as on its spans —
inside Odoo the job is the host, this label is the SDK), its bounded context, its use case, its
layer and its outcome — `ok`, `expected`,
or the failure's kind (`domain`, `invalid`, `not_found`, `unavailable`, `internal`, … the
classification every wire already shares, including what an error's class declares with
`failure_kind = ...`) — and its class in `error.type`. From that one metric a dashboard has the rate
(its count), the errors (its outcome) and the latency (its buckets) of every use case.

```python
class CommandPing(DataTransferObject):
    pass


shop = UseFramework("shop", log_after_execution=False)


@shop.feature(CommandPing)
class Ping(Feature):
    def execute(self, dto: CommandPing) -> None:
        return None


recorder = InMemoryRecorder()  # what a test records into; production uses a backend below
with metrics.using(recorder):
    shop(CommandPing())

((labels, durations),) = recorder.observations("sincpro.use_case.duration")
assert labels == {
    "service.name": "shop",  # the library or service; the bus stands in when none resolves
    "sincpro.context": "shop",
    "sincpro.use_case": "shop.CommandPing",
    "sincpro.layer": "feature",
    "sincpro.outcome": "ok",
    "error.type": "",
}
```

A failure answered by an error handler is still recorded as its kind: the caller got an answer,
the use case did not succeed.

An error the bus was told to expect (`bus.ignore_sentry_exceptions(...)`: a preview that asks
for confirmation, a refused argument) is `expected`, not a failure kind: it is traffic, so an
error-rate alert reads `sincpro_outcome!~"ok|expected"`, while the class stays in `error_type`.

The framework's own pieces count what they do, declaring nothing either:

| Metric | Labels | What it answers |
|---|---|---|
| `sincpro.use_case.duration` (s) | `service.name`, `sincpro.context`, `sincpro.use_case`, `sincpro.layer`, `sincpro.outcome`, `error.type` | rate, errors and latency of every use case |
| `sincpro.cache.outcomes` | `sincpro.namespace`, `sincpro.outcome` (`hit`, `stale`, `computed`, `coalesced`, `invalidated`, `fallback`, `bypassed`) | a fail-safe serving through an outage, a store bypassed |
| `sincpro.idempotency.outcomes` | `sincpro.namespace`, `sincpro.outcome` (`claimed`, `replayed`, `in_progress`, `key_reused`) | the rate of duplicates |
| `sincpro.queue.deliveries` | `messaging.system`, `messaging.destination.name`, `sincpro.settlement`, `sincpro.failure_kind` | acks, retries and dead letters per channel — the queue alert |

## Declared: decorate, and it counts, sums or measures

`of(Command).field` is a reference to a field, written as code: the editor completes it, a rename
renames it, and a field that does not exist raises right there.

```python
@billing.feature(CommandIssueInvoice)
@metrics.counts(by=of(CommandIssueInvoice).currency)
@metrics.sums(of(ResponseIssueInvoice).total, by=of(CommandIssueInvoice).currency, unit="BOB")
@metrics.measures(of(ResponseIssueInvoice).lines, buckets=(1, 5, 10, 50))
class IssueInvoice(Feature):
    """Issue an invoice."""

    def execute(self, dto: CommandIssueInvoice) -> ResponseIssueInvoice:
        return ResponseIssueInvoice(number="F-1", total=dto.total, lines=3)


recorder = InMemoryRecorder()
with metrics.using(recorder):
    for total in ("10", "5"):
        billing(
            CommandIssueInvoice(customer_id="c-1", currency=Currency.BOB, total=Decimal(total)),
            ResponseIssueInvoice,
        )

# Every series also carries who and for whom — release, service, version, tenant — as known
# (correlation.md); here only the service is, and it is the bus.
service = ("service.name", "billing")
assert recorder.totals("billing.issue_invoice.runs") == {(("currency", "BOB"), service): 2}
assert recorder.totals("billing.issue_invoice.total") == {(("currency", "BOB"), service): 15.0}
```

| Decorator | Records, on each **successful** run | Named |
|---|---|---|
| `@metrics.counts(by=)` | one more | `{context}.{use_case}.runs` |
| `@metrics.sums(of(X).field, by=, unit=)` | the field's value, added up (a counter: a negative value is dropped) | `{context}.{use_case}.{field}` |
| `@metrics.measures(of(X).field, by=, unit=, buckets=)` | the field's value, as a distribution | `{context}.{use_case}.{field}` |

`X` is the use case's Command, its Response, or the context type it declares
(`Feature[Command, Response, BillingContext]`): `of(BillingContext)["channel"]` reads the
execution context when the run is recorded. A path into any other DTO or context is refused at
import.
A failed run is not counted here: it is already in `sincpro.use_case.duration`, by kind.

### Labels: the project's choice, with one warning

Every distinct value of a label is one more series in the backend for as long as it lives. A
label that is not an `Enum`, a `Literal` or a `bool` is accepted, and said once, where it is
declared:

```python
from structlog.testing import capture_logs

with capture_logs() as said:
    metrics.counts(by=of(CommandIssueInvoice).customer_id)

assert any("customer_id is str" in line["event"] for line in said)
```

What a service measures by is its decision. A customer or an invoice number is usually better on
the trace (span attributes) or in the logs, where it costs nothing per value.

### Every series says who and for whom

Beside its own labels, every series carries `service.name`, `service.version`, `release` and
the execution's `tenant` (Prometheus: `service_name`, `service_version`, `release`, `tenant`),
added where every measurement passes, on every backend, with no collector. A context key goes on
every series when the bus names it, before it is built, because a backend fixes a series' labels
when it creates it — `UseFramework("billing", metric_labels=["channel"])`, a field reference
`metric_labels=(of(BillingContext)["channel"],)`, or `metric_labels: [channel]` in the settings.
The labels are the process's: every series of every bus carries them. See
[correlation](correlation.md).

## By hand, inside `execute`: named by its attribute

For what a decorator cannot say — a retry, a lookup worth timing, a value computed midway —
declare the instrument on the use case's class. Its name is its attribute:
`pricing.price_order.lookups`, never a string in the code.

```python
class CommandPriceOrder(DataTransferObject):
    channel: Literal["web", "pos"]


class ResponsePriceOrder(DataTransferObject):
    total: Decimal


pricing = UseFramework("pricing", log_after_execution=False)


@pricing.feature(CommandPriceOrder)
class PriceOrder(Feature):
    lookups = metrics.counter(by=of(CommandPriceOrder).channel)
    pricing = metrics.timer()
    discount = metrics.histogram(unit="BOB", buckets=(0, 10, 100))
    in_flight = metrics.up_down()

    def execute(self, dto: CommandPriceOrder) -> ResponsePriceOrder:
        self.in_flight.add(1)
        try:
            with self.pricing.time():  # seconds, with the block's outcome
                self.lookups.add(2, dto)  # labels read off the sources handed in
                self.discount.record(7)
            return ResponsePriceOrder(total=Decimal(93))
        finally:
            self.in_flight.add(-1)


recorder = InMemoryRecorder()
with metrics.using(recorder):
    pricing(CommandPriceOrder(channel="pos"), ResponsePriceOrder)

assert recorder.totals("pricing.price_order.lookups") == {
    (("channel", "pos"), ("service.name", "pricing")): 2
}
((timed, _),) = recorder.observations("pricing.price_order.pricing")
assert timed == {"sincpro.outcome": "ok", "error.type": "", "service.name": "pricing"}
```

| Instrument | Records | Call |
|---|---|---|
| `metrics.counter(by=, unit=)` | only up | `self.x.add(n, *sources)` |
| `metrics.up_down(by=, unit=)` | up and down | `self.x.add(±n, *sources)` |
| `metrics.histogram(by=, unit=, buckets=)` | a distribution | `self.x.record(value, *sources)` |
| `metrics.timer(by=, buckets=)` | seconds, with `sincpro.outcome` | `with self.x.time(*sources):` |

`sources` are the objects the `by=` references are read from — the Command, the Response, a
DTO built midway; each reference reads the one it starts at.

## Example: the tokens a model spends

Two fields of the answer, summed per model — a `Literal` of the models is what keeps the label
bounded:

```python
class CommandAsk(DataTransferObject):
    model: Literal["deepseek-chat", "claude-sonnet"]
    question: str


class ResponseAsk(DataTransferObject):
    answer: str
    input_tokens: int
    output_tokens: int


assistant = UseFramework("assistant", log_after_execution=False)


@assistant.feature(CommandAsk)
@metrics.sums(of(ResponseAsk).input_tokens, by=of(CommandAsk).model, unit="{token}")
@metrics.sums(of(ResponseAsk).output_tokens, by=of(CommandAsk).model, unit="{token}")
class Ask(Feature):
    def execute(self, dto: CommandAsk) -> ResponseAsk:
        return ResponseAsk(answer="…", input_tokens=120, output_tokens=40)


recorder = InMemoryRecorder()
with metrics.using(recorder):
    assistant(CommandAsk(model="deepseek-chat", question="¿saldo?"), ResponseAsk)

assert recorder.totals("assistant.ask.output_tokens") == {
    (("model", "deepseek-chat"), ("service.name", "assistant")): 40
}
```

On Prometheus: `assistant_ask_output_tokens_total{model="deepseek-chat"}`. When the usage is
known only midway — a stream, several calls — record it by hand with a class-level
`metrics.counter(by=of(Usage).model, unit="{token}")` and `self.tokens.add(n, usage)`.

## Who a metric comes from: service, context, version, tenant

| Level | Where | Value |
|---|---|---|
| the service | the resource — `job` on Prometheus via OTLP | `service.name`: the artifact, **without** its version or registry (stable across releases: `sincpro_odoo_mcp` out of an image reference `registry.example.com/sincpro/sincpro_odoo_mcp:0.8.0`); `service.version` and `tenant` (`resource.tenant`, the key every Sincpro signal and the Alloy pipeline read) beside it |
| the bounded context | every series | `sincpro.context` |
| what runs each context | one info series per context | `sincpro.context.info{sincpro_context, sincpro_artifact, sincpro_version, sincpro_tenant} 1` |

Version and tenant are not labels on every series: a version there would start every series
again at each deploy. They travel once, on the info series — the Prometheus `*_build_info`
pattern — and are joined when a query needs them. Both come from the same identity traces and
errors use (`ObservabilityIdentity`, `TENANT`); what is not set is not invented.

```promql
# errors per version of each context
sum by (sincpro_context, sincpro_version) (
  rate(sincpro_use_case_duration_seconds_count{sincpro_outcome!="ok"}[5m])
  * on (job, instance, sincpro_context) group_left (sincpro_version) sincpro_context_info
)
# only one tenant
... * on (job, instance, sincpro_context) group_left () sincpro_context_info{sincpro_tenant="acme"}
```

On Sincpro's cluster the metrics travel over OTLP to Alloy, the same endpoint as the traces, and
Alloy writes them to Prometheus (`otelcol.exporter.prometheus` → `prometheus.remote_write`). There
`service.name` becomes `job`, `service.instance.id` (one per process) becomes `instance`, the rest
of the resource lands on `target_info`, and Alloy copies `resource.tenant` to a `tenant` label on
every series — a constant per deployment, so it adds no series. The resource's `service.version`
can also be promoted to every series (`resource_to_telemetry_conversion`) when one version per
series is wanted; Sincpro's pipeline leaves it off.

## Where it goes: the backend

The process has one recorder; every bus records into it, told apart by `sincpro.context` and by
the context in each declared name.

| `OTEL_METRICS_EXPORTER` | `SINCPRO_METRICS_BACKEND` (alias) | Recorder | Needs |
|---|---|---|---|
| not set (default) | `auto` | OpenTelemetry when a meter provider or `OTEL_EXPORTER_OTLP_ENDPOINT` is there; otherwise nothing | — |
| `otlp` | `otel` | `OtelRecorder`: the host's meter provider, or one exporting to the OTLP endpoint | `[opentelemetry]` |
| `prometheus` | `prometheus` | `PrometheusRecorder`, scraped at `/metrics` | `[prometheus]` |
| `none` (or `OTEL_SDK_DISABLED=true`) | `off` | nothing | — |

`SINCPRO_METRICS_BACKEND` set to anything but `auto` wins. An exporter the framework does not build
(`console`) records nothing. With a collector that does not take metrics, `none` also silences the
exporter's warning every 60 s.

`metrics.use(recorder)` sets it in code; `metrics.using(recorder)` for a block, as the tests do.

### Prometheus — the reference stack

```python
from prometheus_client import CollectorRegistry

from sincpro_framework.observability.metrics.adapters.prometheus import PrometheusRecorder

scraped = PrometheusRecorder(CollectorRegistry())  # the default registry when none is given
with metrics.using(scraped):
    billing(
        CommandIssueInvoice(customer_id="c-1", currency=Currency.USD, total=Decimal(1)),
        ResponseIssueInvoice,
    )

page = scraped.exposition().decode()
assert (
    'billing_issue_invoice_runs_total{currency="USD",release="",service_name="billing",'
    'service_version="",tenant=""} 1.0'
) in page  # an unknown correlation label is empty: to Prometheus, absent
assert "sincpro_use_case_duration_seconds_bucket{" in page
```

Names follow Prometheus' rules: dots become underscores, a counter ends in `_total`, a duration
in `_seconds`, and label keys lose their dots (`sincpro_context`). `FastApiGateway.app()` serves
the scrape at `/metrics` by itself when the process records to Prometheus; elsewhere mount
`metrics.recorder.asgi_app()`, or `metrics.recorder.serve(9464)` for a worker with no HTTP
server. With several worker processes (gunicorn, `uvicorn --workers`), set
`PROMETHEUS_MULTIPROC_DIR` to an empty directory before the process starts: each worker writes
there and the scrape answers their sum.

### OpenTelemetry

`OtelRecorder` records on the OTel meter, so whatever the process configured receives it — OTLP
to a collector, a vendor's exporter, OTel's own Prometheus reader. A meter provider the host
registered (Odoo, auto-instrumentation) is used as it is; with none and an OTLP endpoint set, one
is installed under the process's identity, exporting every 60 s.

### A backend of your own

A backend implements `Recorder` — `add` for counters, `record` for histograms — and proves it
with the contract every recorder of the framework passes:

```python
from sincpro_framework.observability.metrics import Recorder
from sincpro_framework.observability.metrics.testing import RecorderContract

kept = InMemoryRecorder()  # stands in for yours


def total(name, labels):
    return kept.totals(name).get(tuple(sorted(labels.items())), 0)


def count(name, labels):
    return sum(len(values) for found, values in kept.observations(name) if found == labels)


assert isinstance(kept, Recorder)
RecorderContract(kept, total, count).check()
```

A recorder never has to guard itself: every call is shielded, and one that raises is logged
once per instrument and skipped — measuring never fails the use case it measures.

## Reference

| | |
|---|---|
| `metrics.counts(by=)` / `.sums(value, by=, unit=)` / `.measures(value, by=, unit=, buckets=)` | declared on a use case, recorded on each successful run |
| `metrics.counter()` / `.up_down()` / `.histogram()` / `.timer()` | declared on the use case's class, recorded by hand |
| `of(Dto).field` | a field reference: a value or a label, checked where written |
| `metrics.use(recorder)` / `metrics.using(recorder)` / `metrics.recorder` | the process's recorder |
| `declares_metrics(cls)` | whether a use case declared a metric with a decorator |
| `InMemoryRecorder`, `PrometheusRecorder`, `OtelRecorder`, `Recorder` | the recorders; `Recorder` is the port |
| `RecorderContract` (`sincpro_framework.observability.metrics.testing`) | the contract a recorder passes |
| `sincpro.context.info` | which library, version and tenant run each bounded context |
| `OTEL_METRICS_EXPORTER`, `OTEL_SDK_DISABLED` | `otlp` · `prometheus` · `none`; `true` — alias `SINCPRO_METRICS_BACKEND`: `auto` · `otel` · `prometheus` · `off` |
