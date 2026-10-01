---
name: sincpro-framework-observability
description: Instrument a sincpro_framework service — traces (OTLP), errors (Sentry/GlitchTip), correlated logs and metrics (Prometheus), with nothing declared for the basics and decorators for business metrics. Use whenever a task adds tracing, error reporting, logging, metrics, span attributes, dashboards, alerting, or asks why a use case has no span/Sentry event.
---

# sincpro-framework-observability

The bus instruments itself. Extras and environment variables only decide **where** the data goes.
This skill stands alone; the long form is `docs/observability/` (README, metrics, correlation,
span-attributes) and PRD_03 in the framework repo.

| Signal | Recorded | Extra | Destination |
|---|---|---|---|
| Logs with `trace_id`/`span_id` | always | none | stdout / your logger |
| Traces — a span per DTO | always | `[opentelemetry]` | `OTEL_EXPORTER_OTLP_ENDPOINT` |
| Errors — each failure once | always | `[sentry]` | `SENTRY_PYTHON_DSN` |
| Metrics — every use case + declared | always | `[prometheus]` / `[opentelemetry]` | `OTEL_METRICS_EXPORTER` |

## Context

- **Problem it solves:** every use case run on a bus gets a span, an error report, correlated log
  lines and a duration metric, all carrying the same keys (release, service, version, tenant,
  context, use case, outcome, `trace_id`), with no code per use case.
- **Tooling, not policy.** The framework adds no security or PII restrictions: any context key and
  any span attribute is accepted, nothing is refused for its name or content. What a service puts
  on its telemetry is its decision; `hide_in_logs` is the tool to keep a key off every signal.
- **It is not** a collector, a dashboard, an audit trail (use domain events), or an APM agent for
  the HTTP server (the `process` door covers the transport).
- **Do not use** metric labels for values without bound (a customer, an invoice number): put those
  on the span or the context.

## Abstractions

| Term | What it is | Kind | Import |
|---|---|---|---|
| `Observability` | one per bus (`bus.observability`): identity, backends, spans, errors | adapter | `sincpro_framework.observability` |
| `process` (`ProcessObservability`) | the transport's door: `tracer(name)`, `bind_logger(logger)`, `record_error` | adapter | `sincpro_framework.observability` |
| `ObservabilityStatus` / `ComponentStatus` | what came up: `bus.observability.status.sentry.state/.reason` | DTO | `sincpro_framework.observability` |
| `ObservabilityIdentity` | who the **service** is: artifact, version, release, bus | DTO | `sincpro_framework.observability` |
| `bus.with_trace(carrier=...)` / `bus.with_parent_trace()` | adopt an incoming `traceparent` / the host's active span | function | `UseFramework` methods |
| `bus.context({...})` | the execution context — the source of correlation keys | function | `UseFramework` method |
| `UseFramework(hide_in_logs=, metric_labels=)` | keys kept off every signal / context keys on every series | setting | `sincpro_framework` |
| `bus.ignore_sentry_exceptions(*types)` | expected traffic: no GlitchTip, outcome `expected` | setting | `UseFramework` method |
| `traces` → `@traces.attributes(..., namespace=)` / `traces.annotate({...})` | business fields on the use case's span | decorator / function | `sincpro_framework.observability` |
| `of(X).field` / `of(Ctx)["key"]` | a checked reference to a Command/Response field or a context key | function | `sincpro_framework.observability` (same object in `.metrics`) |
| `metrics` (`Metrics`) | the process's metrics: decorators, instruments, recorder choice | registry | `sincpro_framework.observability.metrics` |
| `@metrics.counts` / `.sums` / `.measures` | declared metrics, recorded on success | decorator | same |
| `metrics.counter` / `.up_down` / `.histogram` / `.timer` | instruments declared on the class, recorded in `execute` | function | same |
| `Recorder` | where measurements go | port | same |
| `InMemoryRecorder` | stdlib recorder for tests | adapter | same |
| `PrometheusRecorder` | `prometheus_client`, `/metrics` | adapter (`[prometheus]`) | `sincpro_framework.observability.metrics.adapters.prometheus` |
| `OtelRecorder` | OTel meter (host's or OTLP) | adapter (`[opentelemetry]`) | `sincpro_framework.observability.metrics.adapters.otel` |
| `RecorderContract` | proves a recorder of yours | port (test contract) | `sincpro_framework.observability.metrics.testing` |
| GlitchTip/Sentry client, OTLP tracer provider | built per bus, internal | adapter (`[sentry]`, `[opentelemetry]`) | none — configured by env |
| `APP_RELEASE`, `TENANT`, `SENTRY_PYTHON_DSN`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_TRACES_SAMPLER_ARG`, `OTEL_METRICS_EXPORTER` (alias `SINCPRO_METRICS_BACKEND`), `OTEL_TRACES_EXPORTER`, `OTEL_SDK_DISABLED`, `OTEL_RESOURCE_ATTRIBUTES`, `SINCPRO_FRAMEWORK_LOG_LEVEL` | the framework's own settings | setting | environment / settings YAML |

Look-alikes: `ObservabilityIdentity` (the service) is not `auth.Identity` (the caller — whose
`tenant` and `subject` feed `tenant`/`user_id`). `hide_in_logs` keeps a key off **all four**
signals, not only logs. `metric_labels` puts a context key on every series; `by=` labels one
declared metric. `traces.annotate` reaches only the span; `bus.context`/`self.context` reach every
signal. `with_trace` opens a root span under the bus; `with_parent_trace` makes the bus span a
direct child of the host's.

## Architecture

**Inside the framework** — package `sincpro_framework/observability/`:

- `api.py` the two doors (`Observability` per bus, module-level `process`); `correlation.py` the
  keys on every signal; `registry.py` providers and clients by bus; `tracing/`, `errors/` the
  OpenTelemetry and Sentry adapters; `metrics/` — `domain/` (instruments, `Recorder` port),
  `adapters/` (`in_memory` stdlib; `prometheus`, `otel` optional), `entrypoint/` (decorators),
  `infrastructure/` (recorder choice, per-run measurement).
- `opentelemetry`, `sentry_sdk` and `prometheus_client` are optional extras, imported only there.
  Missing, or with no endpoint/DSN, every signal but logs is a no-op; measuring never fails a use
  case.
- **A bus never takes OpenTelemetry's global provider.** Each bus builds its own `TracerProvider`.
  When nothing holds the global, the framework installs one under the **process** identity (no bus)
  for transport instrumentation; a host's global (Odoo, an operator's agent) is left alone, and a bus
  with no endpoint rides it. GlitchTip: an isolated `sentry_sdk.Client` per release — never
  `sentry_sdk.init()`, the host's client is untouched. Metrics: one recorder per process.

**Inside a consumer service** (one `UseFramework` per bounded context, created in
`infrastructure/framework.py` before `services/` is imported):

```
billing/
  infrastructure/framework.py   # UseFramework("billing", hide_in_logs=[...], metric_labels=[...])
                                #   + instance.ignore_sentry_exceptions(ExpectedError, AuthError)
  services/issue_invoice.py     # @metrics.* and @traces.attributes on the use case; traces.annotate in execute
  entrypoints/                  # gateways adopt traceparent by themselves; FastApiGateway.app() serves /metrics
main.py / asgi.py               # process.bind_logger(server_logger) — a sincpro_log logger; trace_id on access lines
pyproject.toml                  # sincpro-framework[opentelemetry,sentry,prometheus] — only what is used
deployment env                  # APP_RELEASE, TENANT, SENTRY_PYTHON_DSN, OTEL_EXPORTER_OTLP_ENDPOINT, OTEL_METRICS_EXPORTER
```

**One call:**

```
bus(dto) ─► span "context/DTO" opens (parent: with_trace carrier, host span, or none)
   │         log lines: trace_id, span_id, service_name, release, tenant, every context key
   │         @traces.attributes read off the Command
   ▼
 execute() ─► traces.annotate(...) · self.x.add(...) · self.context[...] = ...
   ├─ ok      → attributes off the Response; counts/sums/measures; duration{outcome=ok}
   └─ failure → span error + outcome; GlitchTip once (unless ignored → outcome=expected);
                logged once by the outermost bus; duration{outcome=<kind>}
   ▼
 span ─► the bus's TracerProvider ─► OTLP      measurements ─► the process recorder ─► /metrics | OTLP
```

## Mistakes an agent makes

Silent ones first — the runtime says nothing.

- **Work off the bus.** A helper that takes the bus, a function imported from another `services/`
  file, a lambda dependency: no span, no GlitchTip event, no metric. Every use case is a Feature or
  ApplicationService executed through `bus(dto)`.
- **A destination silently off.** `SENTRY_DSN` instead of `SENTRY_PYTHON_DSN`, a DSN without `@`,
  only `opentelemetry-api` installed, no `OTEL_EXPORTER_OTLP_ENDPOINT`, or `sentry_sdk.init()`
  expected to serve the bus. Read `bus.observability.status` (or the `observability sentry=…
  otel=…` line logged when the bus is built).
- **No `APP_RELEASE` on a deployed service.** The signals carry no release; the service name falls
  back to the calling library or the bus name. Set it (`registry/…/artifact:version`).
- **An error handler that only watches.** What it returns becomes the bus's answer: `None`, and the
  failure is gone for the caller. End it with `raise`.
- **Expecting a context key on metrics.** It is on logs, spans and GlitchTip by itself; on metrics
  only when named in `metric_labels` (bus or settings).
- **An unbounded `by=` label.** Accepted with one warning; each value is one more series. Put it on the
  span (`traces.attributes`) instead.
- **`traces.annotate` outside a use case** (import time, a raw thread, an adapter called from
  outside the bus): nothing happens. A value every signal needs goes in `self.context[...]`.
- **Prometheus expected from `auto`.** `auto` never picks Prometheus: set
  `OTEL_METRICS_EXPORTER=prometheus` and serve `/metrics` (`FastApiGateway.app()` does); with
  several worker processes set `PROMETHEUS_MULTIPROC_DIR` before start.
- **Expected traffic left as failures.** Validation, "already exists", auth refusals: in
  `bus.ignore_sentry_exceptions(...)`, or every error-rate alert fires on normal use.

## Metrics — by itself, then by declaration

Every use case lands on `sincpro.use_case.duration` (seconds) labelled with service, context, use
case, layer and outcome (`ok`, `expected`, or the failure's kind). More is declared on the use case
— **names come from the context, the use case and the field, never a string**:

```python
from sincpro_framework.observability.metrics import metrics, of

@billing.feature(CommandIssueInvoice)
@metrics.counts(by=of(CommandIssueInvoice).currency)                 # billing.issue_invoice.runs
@metrics.sums(of(ResponseIssueInvoice).total, by=of(CommandIssueInvoice).currency, unit="BOB")
@metrics.measures(of(ResponseIssueInvoice).lines, buckets=(1, 5, 10, 50))
class IssueInvoice(Feature): ...
```

`of(Command).field` is checked where written; `of(BillingContext)["channel"]` reads the typed
execution context. Every series also carries release, service, version and the execution's tenant.
Instruments by hand, backends and Prometheus: [references/metrics.md](references/metrics.md).

## Errors

**Expected** traffic goes in `bus.ignore_sentry_exceptions(...)` — kept out of GlitchTip, logged at
info, an `expected` outcome. Anything else reaches GlitchTip once, even when an error handler
answers for it. Error handlers by layer:
[references/traces-errors-logs.md](references/traces-errors-logs.md).

## Spans and correlation

```python
from sincpro_framework.observability import of, traces

@billing.feature(CommandIssueInvoice)
@traces.attributes(of(CommandIssueInvoice).customer_nit, namespace="billing",
                   channel=of(BillingContext)["channel"])        # billing.customer_nit, billing.channel
class IssueInvoice(Feature):
    def execute(self, dto: CommandIssueInvoice) -> ResponseIssueInvoice:
        traces.annotate({"billing.document": "F-1"})              # known only midway
        ...
```

- `namespace=` is required; any key is accepted, Tempo filters by it without a series per value.
- `with bus.with_trace(carrier=headers) as traced: traced(dto)` adopts an incoming `traceparent`;
  the entrypoints do this by themselves.
- **The execution context is on every signal.** Every key of `bus.context({...})` — or of what an
  interceptor, a hook or `self.context` wrote — goes on the log line, the span and the GlitchTip
  event. The tenant is the execution's (context → authenticated identity → `TENANT` →
  `OTEL_RESOURCE_ATTRIBUTES`); `user_id` (context → identity) is also GlitchTip's user.

Keys per signal and filtering across Prometheus, Tempo, Loki and GlitchTip:
[references/correlation.md](references/correlation.md).

## References

- [references/traces-errors-logs.md](references/traces-errors-logs.md) — spans, error handlers, logs, embedding in a host (Odoo)
- [references/metrics.md](references/metrics.md) — instruments, labels, recorders, Prometheus/OTLP
- [references/correlation.md](references/correlation.md) — the keys on every signal, `APP_RELEASE`, filtering

## Related

- Use cases on the bus, error handlers, the context manager: `sincpro-framework`, `sincpro-framework-core`
- Who is calling (tenant, `user_id` from the identity): `sincpro-framework-auth`
- The SQLAlchemy adapter reports every statement: `sincpro-framework-persistence`
