# Correlation: the same keys on logs, traces, metrics and GlitchTip

A use case that runs leaves a point on a metric, a span, log lines and — when it fails — a
GlitchTip issue. The framework puts **the same keys** on all four, from its own configuration and
from the **execution context**, so a dashboard's variables (`$release $service $version $tenant
$context $use_case`) filter every panel whatever the datasource, and a `trace_id` opens the span,
its lines and its issue.

The execution context is the framework's language: the entrypoint, the application
(`bus.context({...})`), an interceptor, a hook and the handler (`self.context`) all write to it,
and **every key of it goes on every signal** — nothing to declare. The only filter is the one the
project chooses: `hide_in_logs` keeps a key off all four. The rule is
[PRD 03 §4.10](../prd/PRD_03_observability-tracing.md).

It does not depend on where the service runs: Kubernetes, another collector, an on-premise machine
with nothing, or an SDK inside Odoo. The collector (Alloy) may add labels of its own — it still
does — but nothing here needs it. The design of each signal is in
[PRD 03](../prd/PRD_03_observability-tracing.md); this page is how they join.

## 1. The keys

| Concept | Value (an SDK inside Odoo, tenant `dispel`) | Comes from — nothing new to configure |
|---|---|---|
| service | `sincpro-siat-soap` | the library that built the bus; else `APP_RELEASE` without registry or tag |
| version | `8.0.4` | the library's installed version; else the tag of `APP_RELEASE` |
| release | `registry…/sincpro_odoo_mcp:0.8.0` | `APP_RELEASE` verbatim, else `artifact:version` — the artifact exactly as it was shipped |
| tenant | `dispel` | the execution's: the context's `tenant`; else the authenticated identity's; else the deployment's (`TENANT`, then `tenant` in `OTEL_RESOURCE_ATTRIBUTES`) |
| user | `user:42` | the execution's: the context's `user_id`; else the authenticated identity's subject |
| any context key | `company=acme-srl` | what `bus.context`, an interceptor, a hook or `execute` put there |
| context | `siat-soap-sdk` | the bus (`UseFramework` name) |
| use case | `CommandSendInvoice` | the DTO's class |
| outcome | `ok` · `expected` · `unavailable` · `internal` · … | how the run ended — `expected` for what the bus ignores (`ignore_sentry_exceptions`), else the failure's kind |
| error | `ConnectionError` | the exception's class (`__name__`) |
| trace id | 32 hex | the active span |

A library inside a host keeps its own name and version even when the host sets `APP_RELEASE` and
`OTEL_SERVICE_NAME`: the library that built the bus wins. There is no environment key: each
environment (staging, production) has its own monitoring stack, so the backend already says it.

### From `APP_RELEASE`

| `APP_RELEASE` | service | version | GlitchTip release |
|---|---|---|---|
| `registry.digitalocean.com/sincpro/sincpro_odoo_mcp:0.8.0` | `sincpro_odoo_mcp` | `0.8.0` | as deployed |
| `sincpro-siat-soap:8.0.1` | `sincpro-siat-soap` | `8.0.1` | as deployed |
| `registry.local:5000/team/app` (a port, no tag) | `app` | — | as deployed |
| `registry.io/team/app:1.2@sha256:…` (a digest) | `app` | `1.2` | as deployed |
| `app@1.2.3` | `app` | `1.2.3` | as deployed |
| none, a library | its distribution | its version | `name:version` |

What the deployment declares in `OTEL_RESOURCE_ATTRIBUTES` (`tenant`, `service.version`) wins over
what the framework derives.

## 2. Where each key is, on each signal

| Concept | Logs (JSON field) | Traces | Metrics (Prometheus label) | GlitchTip |
|---|---|---|---|---|
| release | `release` | resource `release` | `release` | the release, and tag `release` |
| service | `service_name` | resource `service.name` | `service_name` (and `job`) | tag `service_name` |
| version | `service_version` | resource `service.version` | `service_version` | tag `service_version`, `sincpro.version`; inside the release |
| tenant | `tenant` | attribute `tenant` (the execution's); resource `tenant` (the deployment's) | `tenant` (the execution's) | tag `tenant` (the execution's); `environment` (the deployment's) |
| user | `user_id` | attribute `user_id` | — unless the bus declares it | tag `user_id`, and the event's user |
| any context key | the key | attribute of the same name | when the bus declares it (`metric_labels`) | tag of the same name |
| context | `sincpro_context` | `sincpro.context`; the span's name `context/DTO` | `sincpro_context` | tag `sincpro.context` |
| use case | `sincpro_use_case` | `sincpro.use_case` | `sincpro_use_case` | tag `sincpro.use_case` |
| layer | `sincpro_layer` | `sincpro.layer` | `sincpro_layer` | tag `sincpro.layer` |
| outcome | `sincpro_outcome` (failure line) | `sincpro.outcome` | `sincpro_outcome` | tag `sincpro.outcome` |
| error | `error_type` | `error.type` | `error_type` | tag `error.type` |
| trace id | `trace_id`, `span_id` | the span | exemplar on each point | tag `trace_id` + the event's trace context |

A log field is spelled as the Prometheus label is: the same text filters Loki and Prometheus. An
unknown value is left off; on Prometheus, whose series have fixed labels, it is empty — which
Prometheus reads as absent.

### Metric labels from the context

A backend fixes a series' labels when it creates the instrument, so the context keys that go on
every series are named before the bus is built: `UseFramework("billing",
metric_labels=["company"])`, a field reference `metric_labels=(of(BillingContext)["company"],)`,
or `metric_labels: [company]` in the settings. They are the process's: every series of every bus
carries them. A use case can also label one of its own metrics by the context:
`@metrics.counts(by=of(BillingContext)["channel"])`.

### A value counts from the moment it is set

A log line reads the context when it is written. The span, the metric and the GlitchTip event
read it when they are recorded — at the end of the use case — so a key an interceptor, a hook or
`execute` set midway is on them, including one set in a `bus.context(...)` scope that closed
before the use case did. A nested bus and `thread_context()` inherit the context; a queue message
hands its `correlationid` and a `tenant` header to it.

**Kept as they were**, because dashboards and alerts read them: the log's `app_name`, `dto`,
`failed_in`, `layer`; the span's `sincpro.instance`; GlitchTip's `release`, `environment`,
`sincpro.instance`, `sincpro.dto`, `sincpro.package`, `server_name`. **Changed in value**: the
span's `service.name` (the version and the bus are keys of their own) and its name (`context/DTO`).

### What the collector adds, and what it no longer has to

| | Framework | Alloy (Kubernetes) |
|---|---|---|
| Loki labels (`service_name`, `tenant`, `env`, `version`, `instance`, `job`, `level`) | — | from the pod's labels, as before |
| identity and coordinates in the line itself | every line | — |
| tenant on traces and metrics | span attribute and every series (the execution's), resource (the deployment's) | `metrics_tenant` sets it only where absent — a no-op for framework metrics |

On Loki the pod's `service_name` label is the bus (from `app_name`); the line's own `service_name`
field is the artifact and reads as `service_name_extracted` after `| json`. Without Alloy — an
on-premise install — the fields are still in every line.

## 3. Filtering: one question, every tool

"Is SIAT 8.0.4 failing for dispel?"

| Tool | Filter |
|---|---|
| Prometheus | `sum by (sincpro_use_case, sincpro_outcome) (rate(sincpro_use_case_duration_seconds_count{service_name="sincpro-siat-soap", tenant="dispel", sincpro_outcome!~"ok\|expected"}[5m]))` |
| Prometheus, by version | `...{service_name="sincpro-siat-soap", service_version="8.0.4", tenant="dispel"}` — or, as before, `group_left (sincpro_version) sincpro_context_info` |
| Tempo | `{resource.service.name="sincpro-siat-soap" && resource.service.version="8.0.4" && span.tenant="dispel" && status=error}` |
| Tempo, where it started | `{resource.service.name="sincpro-siat-soap" && status=error} \| rate() by (resource.service.version)` |
| Loki | `{tenant="dispel"} \| json \| service_name_extracted="sincpro-siat-soap" \| service_version="8.0.4" \| sincpro_outcome!="ok"` |
| GlitchTip | `service_name:sincpro-siat-soap service_version:8.0.4 tenant:dispel` |

A dashboard with `$service $version $tenant $context $use_case` writes those same filters in each
panel; changing `$tenant` changes all of them. From any failure, `trace_id` is the jump: the metric
point's exemplar, the log line, the GlitchTip tag — to the span, and from the span to its lines.

Questions the keys answer: *a bump or a bug?* — the error rate by `service.version`; *one customer
or all?* — by `tenant`; *which use case?* — by `sincpro_use_case`; *our code or a dependency?* —
`sincpro_outcome` (`unavailable` is a dependency that did not answer, `domain` a business rule,
`internal` a bug).

## 4. With nowhere to send

The same code runs everywhere; only the destination changes. With no OpenTelemetry installed, no
endpoint or no DSN, spans, metrics and GlitchTip are no-ops and the log lines keep every key. A
measurement or a report never fails the use case, and the business error reaches the caller
unchanged.

## 5. Guarded by

`tests/observability/test_correlation_contract.py` runs one failure and reads the four signals at
once: the same service, version, tenant, context, use case, outcome, error and `trace_id` on the
log line, the span, the metric and the GlitchTip event; an SDK inside Odoo keeps its name.
`tests/observability/test_execution_correlation.py` holds the execution context to it: the release
and the tenant on every series, two tenants in one process, every context key on the span and the
event, `hide_in_logs` on all four, a key set midway, the metric labels a bus declares and
`of(ContextType)["key"]`. The
shapes of `APP_RELEASE` are in `tests/observability/tracing/test_service_name.py`; what the
deployment declares winning, in `tests/observability/tracing/test_provider_contract.py`.
