# Correlation

The framework puts **the same keys** on logs, traces, metrics and GlitchTip, from its own
configuration, so a dashboard's variables filter every panel and a `trace_id` opens the span, its
lines and its issue. Depth: `docs/observability/correlation.md`.

| Concept | Value | Comes from — nothing new to configure |
|---|---|---|
| service | `sincpro-siat-soap` | the library that built the bus; else `APP_RELEASE` without registry/tag |
| version | `8.0.4` | the library's installed version; else the tag of `APP_RELEASE` |
| tenant | `dispel` | `TENANT`, or `tenant` in `OTEL_RESOURCE_ATTRIBUTES` |
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
| service | `service_name` | resource `service.name` | `service_name` (and `job`) | tag `service_name` |
| version | `service_version` | resource `service.version` | on `sincpro_context_info`, joined | tag + release |
| tenant | `tenant` | resource `tenant` | `tenant` | tag `tenant` |
| context | `sincpro_context` | `sincpro.context` | `sincpro_context` | tag |
| use case | `sincpro_use_case` | `sincpro.use_case` | `sincpro_use_case` | tag |
| layer | `sincpro_layer` | `sincpro.layer` | `sincpro_layer` | tag |
| outcome | `sincpro_outcome` | `sincpro.outcome` | `sincpro_outcome` | tag |
| error | `error_type` | `error.type` | `error_type` | tag |
| trace id | `trace_id`, `span_id` | the span | exemplar | tag + trace context |

A log field is spelled as the Prometheus label is, so the same text filters Loki and Prometheus.
**Version and tenant are not labels on every series** (a version there would restart every series at
each deploy): they travel once on `sincpro.context.info` and are joined when a query needs them.

## Filtering — one question, every tool

"Is SIAT 8.0.4 failing for dispel?"

```promql
sum by (sincpro_use_case, sincpro_outcome) (
  rate(sincpro_use_case_duration_seconds_count{service_name="sincpro-siat-soap", tenant="dispel", sincpro_outcome!~"ok|expected"}[5m])
)
* on (job, instance, sincpro_context) group_left (sincpro_version)
  sincpro_context_info{sincpro_version="8.0.4"}
```

Tempo: `{resource.service.name="sincpro-siat-soap" && resource.service.version="8.0.4" && status=error}`.
Loki: `{tenant="dispel"} | json | service_name_extracted="sincpro-siat-soap" | sincpro_outcome!="ok"`.
GlitchTip: `service_name:sincpro-siat-soap sincpro.version:8.0.4 tenant:dispel`.

Questions the keys answer: *bump or bug?* (error rate by `service.version`); *one customer or all?*
(by `tenant`); *which use case?* (by `sincpro_use_case`); *our code or a dependency?*
(`sincpro_outcome`).

## With nowhere to send

The same code runs everywhere; only the destination changes. With no OpenTelemetry installed, no
endpoint or no DSN, spans, metrics and GlitchTip are no-ops and the log lines keep every key. A
measurement or a report never fails the use case, and the business error reaches the caller
unchanged.
