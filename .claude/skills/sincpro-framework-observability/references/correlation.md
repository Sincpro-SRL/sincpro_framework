# Correlation

The framework puts **the same keys** on logs, traces, metrics and GlitchTip, from its own
configuration and from the **execution context**, so a dashboard's variables filter every panel
and a `trace_id` opens the span, its lines and its issue. Depth: `docs/observability/correlation.md`;
the rule: PRD_03 §4.10.

**The context is the source.** Whatever the entrypoint, `bus.context({...})`, an interceptor, a
hook or `self.context` puts in the execution context goes on the log line, the span and the
GlitchTip event — nothing to declare, nothing refused for its name. `hide_in_logs` is the only
filter, and it keeps a key off all four signals. A value counts from the moment it is set: the
span, the metric and the event are recorded at the end of the use case.

| Concept | Value | Comes from — nothing new to configure |
|---|---|---|
| service | `sincpro-siat-soap` | the library that built the bus; else `APP_RELEASE` without registry/tag |
| version | `8.0.4` | the library's installed version; else the tag of `APP_RELEASE` |
| release | `registry…/sincpro_odoo_mcp:0.8.0` | `APP_RELEASE` verbatim, else `artifact:version` |
| tenant | `dispel` | the execution's: context `tenant` → authenticated identity's → `TENANT` → `tenant` in `OTEL_RESOURCE_ATTRIBUTES` |
| user | `user:42` | context `user_id` → authenticated identity's subject |
| any context key | `company=acme-srl` | what the execution context carries |
| context | `siat-soap-sdk` | the bus (`UseFramework` name) |
| use case | `CommandSendInvoice` | the DTO's class |
| outcome | `ok` · `expected` · `unavailable` · `internal` · … | how the run ended |
| error | `ConnectionError` | the exception's class |
| trace id | 32 hex | the active span |

A library inside a host keeps its own name and version even when the host sets `APP_RELEASE` /
`OTEL_SERVICE_NAME`: the library that built the bus wins. There is no environment key — each
environment has its own monitoring stack.

## Where each key is

| Concept | Logs (JSON field) | Traces | Metrics (label) | GlitchTip |
|---|---|---|---|---|
| release | `release` | resource `release` | `release` | release + tag |
| service | `service_name` | resource `service.name` | `service_name` (and `job`) | tag `service_name` |
| version | `service_version` | resource `service.version` | `service_version` | tag + release |
| tenant | `tenant` | attribute `tenant` (execution); resource (deployment) | `tenant` (execution) | tag `tenant` |
| user | `user_id` | attribute `user_id` | only if declared | tag + the event's user |
| context key | the key | attribute | only if declared (`metric_labels`) | tag |
| context | `sincpro_context` | `sincpro.context` | `sincpro_context` | tag |
| use case | `sincpro_use_case` | `sincpro.use_case` | `sincpro_use_case` | tag |
| layer | `sincpro_layer` | `sincpro.layer` | `sincpro_layer` | tag |
| outcome | `sincpro_outcome` | `sincpro.outcome` | `sincpro_outcome` | tag |
| error | `error_type` | `error.type` | `error_type` | tag |
| trace id | `trace_id`, `span_id` | the span | exemplar | tag + trace context |

A log field is spelled as the Prometheus label is, so the same text filters Loki and Prometheus.
**Release, service, version and tenant are on every series**, added by the framework on every
backend. A context key goes on every series only when named before the build, because a backend
fixes a series' labels when it creates it: `UseFramework("billing", metric_labels=["company"])`,
`metric_labels=(of(BillingContext)["company"],)`, or `metric_labels: [company]` in the settings.

**A typed context in decorators**: `of(BillingContext)["channel"]` (a `TypedDict`, the same the
handler declares in `Feature[C, R, BillingContext]`) works in `@metrics.counts(by=...)` and
`@traces.attributes(...)`; a key the TypedDict lacks fails at import.

## Filtering — one question, every tool

"Is SIAT 8.0.4 failing for dispel?"

```promql
sum by (sincpro_use_case, sincpro_outcome) (
  rate(sincpro_use_case_duration_seconds_count{service_name="sincpro-siat-soap", service_version="8.0.4", tenant="dispel", sincpro_outcome!~"ok|expected"}[5m])
)
```

Tempo: `{resource.service.name="sincpro-siat-soap" && resource.service.version="8.0.4" && span.tenant="dispel" && status=error}`.
Loki: `{tenant="dispel"} | json | service_name_extracted="sincpro-siat-soap" | sincpro_outcome!="ok"`.
GlitchTip: `service_name:sincpro-siat-soap service_version:8.0.4 tenant:dispel`.

Questions the keys answer: *bump or bug?* (error rate by `service.version`); *one customer or all?*
(by `tenant`); *which use case?* (by `sincpro_use_case`); *our code or a dependency?*
(`sincpro_outcome`).

## With nowhere to send

The same code runs everywhere; only the destination changes. With no OpenTelemetry installed, no
endpoint or no DSN, spans, metrics and GlitchTip are no-ops and the log lines keep every key. A
measurement or a report never fails the use case, and the business error reaches the caller
unchanged.
