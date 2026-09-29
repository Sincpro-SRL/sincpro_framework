# PRD_03: Observability — traces, errors, logs and metrics

- **Status**: built. Traces, errors and logs since the observability refactor; metrics (§4) built
  with this revision — automatic, declared and by hand, on Prometheus (the reference stack) or
  OpenTelemetry. What is left is §10.
- **Extras**: none for the core. `[opentelemetry]` (traces, OTLP metrics), `[sentry]` (errors to
  GlitchTip/Sentry), `[prometheus]` (metrics scraped at `/metrics`).
- **Code**: `sincpro_framework.observability` (the two doors: `Observability` per bus,
  `process` for the transport) and `sincpro_framework.observability.metrics`.
- **Guides**: [observability](../observability/README.md), [metrics](../observability/metrics.md).

This document is the current design and why it is so. It is rewritten when the design changes;
it keeps no history.

---

## 1. Principles

1. **The bus always instruments itself; configuration only decides where the data goes.** No
   flag turns a signal on: an extra plus an endpoint, a DSN or a backend name sends it
   somewhere; without them every signal is a no-op and the bus runs exactly as it would without
   observability.
2. **Nothing observability does can fail a use case.** Every span, event, measurement and log
   call is shielded; a failing exporter is logged, never raised. The use case's own exception
   always propagates — an exporter never swallows it.
3. **The host comes first.** A tracer provider, meter provider or Sentry client the host
   application registered (Odoo, FastAPI instrumentation, an operator's auto-instrumentation) is
   used or left alone, never replaced.
4. **One identity, resolved once.** The Tempo `service.name`, the GlitchTip release and the
   metrics resource are read from the same `ObservabilityIdentity`, so they never disagree.
5. **The bounded context is the unit.** Every signal says which bus it came from:
   `service.name` ends in the bus, errors carry `sincpro.instance`, metrics `sincpro.context`
   and a context prefix on every declared name.
6. **Nothing is named by a string the code can drift from.** Spans are the DTO's name; declared
   metrics are the context, the use case and a field or attribute; labels are field references.
7. **The core imports no backend.** `opentelemetry`, `sentry_sdk` and `prometheus_client` are
   imported only inside their adapters; `tests/test_core_without_extras.py` blocks every one of
   them and runs the whole core.

## 2. Identity and status

`ObservabilityIdentity(artifact, version, bus)` is who is emitting, resolved on first use — the
first source that names an artifact wins, and a source contributes its artifact **and its own
version**:

1. what the caller declared (`UseFramework(name, package=...)`), an escape hatch;
2. the installed library that built the bus (module → distribution by the `_` → `-` convention);
3. the deployment: `APP_RELEASE`, else `OTEL_SERVICE_NAME`;
4. a distribution whose import name differs from its package name (a ~200 ms scan, last).

| Derived | Value | Example |
|---|---|---|
| `service_name` — the service on every signal (span resource, metric `service.name`, log `service_name`, GlitchTip tag) | the artifact without registry, version or bus; the bus when no artifact resolves | `sincpro-siat-soap`, `sincpro_odoo_mcp` |
| `service_version` — `service.version`, log `service_version`, tag `sincpro.version` | the library's installed version, else the tag of `APP_RELEASE` | `8.0.4`, `0.8.0` |
| `bus` — the bounded context: `sincpro.context` everywhere | the `UseFramework` name | `siat-soap-sdk`, `sincpro-sales-mcp` |
| `release` (GlitchTip) | `APP_RELEASE` verbatim, else `artifact:version` — never the bus | `registry.digitalocean.com/sincpro/sincpro_odoo_mcp:0.8.0`, `sincpro-siat-soap:8.0.3` |

`APP_RELEASE` is read as an image reference: the tag after the last `/` is the version (a
registry's `:5000` is a port), a digest (`@sha256:…`) is dropped, `name@version` is accepted. The
release keeps it whole — the registry and the tag, as deployed.

`framework.observability.status` probes each backend as `ComponentStatus(state, reason)` —
`on:init`, `on:host`, `off:sdk_missing`, `off:no_endpoint`, `off:dsn_missing`, `failed:<why>` —
logged once when the bus is built. Errors of the framework itself report under the framework's
own artifact and version, keeping the bus.

**Why**: the release and the service name used to be resolved separately and drifted (a library
inside Odoo reported Odoo's name against its own version); values used to be rewritten (`-` →
`_`), mangling both the name to search for and the version. The service name used to carry the
version and the bus (`sincpro-siat-soap:8.0.4:siat-soap-sdk`): one library appeared under three
names in Tempo and every release added one; the version and the context are keys of their own now
([correlation](../observability/correlation.md)).

## 3. Traces

- **A tracer provider per bus**, so each bus's spans carry its library's `service.name` even
  inside a host that registered a global provider — an SDK in Odoo is found as the SDK. Its
  resource: `service.name`, `service.version`, `tenant`; what the deployment declared in
  `OTEL_RESOURCE_ATTRIBUTES` wins over what the framework derives. Built when the bus is built,
  only with `OTEL_EXPORTER_OTLP_ENDPOINT` set; otherwise a real host provider is ridden
  (`on:host`). The scope is `sincpro_framework` with the framework's version.
- **A process provider for the transport.** ASGI, httpx and uvicorn instrumentation ask for the
  global tracer; the framework installs one under the **process** identity (no bus segment) only
  when nobody owns the global — so an HTTP request is never exported as if it belonged to one
  bounded context. `sincpro_framework.observability.process` is that door: `tracer(...)`,
  `bind_logger(...)`, `record_error(...)`, `was_reported(error)`.
- **A span per DTO execution**, named `context/DTO` (`sincpro-chatter-mcp/CommandListMessages`),
  as an RPC span is named `service/method`, with `sincpro.context`, `sincpro.use_case`,
  `sincpro.layer` (`feature` / `application_service`), `sincpro.outcome`, `sincpro.instance`
  (the bus, kept) and `sincpro.replaces` when a handler replaced another. An ApplicationService's
  Features are its children; an active outer span (FastAPI, Celery, Odoo) is adopted as parent
  through OTel's context.
- **The outcome is the metrics'**: a span starts `ok`; every span a failure crosses takes the
  failure's outcome (`expected` or its kind), as every run's metric does.
- **A failure is recorded once**, on the span of the handler that raised it, with where it
  failed and `error.type` (the class's `__name__`, as every signal spells it); every outer span
  it crosses only takes the error status.
- **Sampling**: `ParentBased(TraceIdRatioBased(OTEL_TRACES_SAMPLER_ARG))` — a decision taken
  upstream always wins, so a sampled request is never cut halfway.
- **Blocks**: `with bus.with_trace(trace_id=, span_id=, carrier=)` starts (or continues, from a
  W3C `traceparent` carrier) a trace for a block; `with bus.with_parent_trace()` adopts the
  active host span without creating one. Both bind the ids to the logger and put `trace_id` /
  `span_id` in `self.context`. Without OTel installed they generate UUIDs, so logs still
  correlate.

## 4. Metrics

### 4.1 What was studied, and what was taken

| Reference | What it does | Taken |
|---|---|---|
| **Micrometer** (Spring): `@Timed`, `@Counted`, `@MeterTag`, the Observation API | annotations on a method; tags from its arguments; one instrumentation yields spans and metrics | decorators on the use case; labels read off its arguments; the bus already has both the span and the measurement of each run |
| **OpenTelemetry** metrics API and semantic conventions | counters, up-down counters, histograms; dotted lowercase names; units as metadata; durations in seconds; `error.type` on failures | the instrument model, the naming, `s` for durations, `error.type` |
| **Prometheus** (`prometheus_client`) | pull; `_total`, `_seconds`; labels fixed at creation; cardinality is the one way to take it down; multiprocess mode for forked workers | the reference adapter; strict bounded labels; label keys fixed per instrument; `PROMETHEUS_MULTIPROC_DIR` |
| **.NET `System.Diagnostics.Metrics`** (`IMeterFactory`) | meters per component, created through the container | instruments bound to the bus that runs the use case |
| **prometheus-fastapi-instrumentator**, django-prometheus | RED metrics of every request with no code | RED of every use case, of every wire, with no code — one level deeper than the request |
| **OTel Collector `spanmetrics`** | metrics derived from spans | kept as an option for the infrastructure; the framework records its own so a service without a collector has them |

### 4.2 Recorded by itself — declaring nothing

| Metric | Kind | Labels |
|---|---|---|
| `sincpro.use_case.duration` | histogram, `s` | `sincpro.context`, `sincpro.use_case`, `sincpro.layer`, `sincpro.outcome`, `error.type` |
| `sincpro.cache.outcomes` | counter | `sincpro.namespace`, `sincpro.outcome` |
| `sincpro.idempotency.outcomes` | counter | `sincpro.namespace`, `sincpro.outcome` |
| `sincpro.queue.deliveries` | counter, `{delivery}` | `messaging.system`, `messaging.destination.name`, `sincpro.settlement`, `sincpro.failure_kind` |
| `sincpro.context.info` | up-down, 1 per context | `sincpro.context`, `sincpro.artifact`, `sincpro.version`, `sincpro.tenant` (§4.9) |

- `sincpro.outcome` is `ok`, `expected`, or the failure's kind — the refined classification
  every wire already encodes (`transport.failures.refined_failure_kind`): what the error's class
  declares (`failure_kind = NOT_FOUND`, `EXHAUSTED`, `UNAVAILABLE`), the idempotency refusals
  (`in_progress`, `key_reused`), then `domain`, `invalid`, `unauthenticated`,
  `permission_denied`, `conflict`, `internal`. A failure an error handler answered is still its
  kind. `error.type` is the exception's class, empty on success.
- **`expected` is an error the bus was told is traffic, not a bug** (`ignore_sentry_exceptions`):
  a preview that asks for confirmation, a refused argument, a rejected credential. Counted as
  `internal` it makes every error-rate alert fire on normal use, so it is kept apart — its class
  still in `error.type` — and the alert reads `outcome!~"ok|expected"`. Which errors are
  expected is one declaration per bounded context, the same that keeps them out of GlitchTip.
- One histogram gives the three signals a use case is watched by: its rate (the count), its
  errors (the outcome) and its latency (the buckets) — for every use case of every bounded
  context, every wire included, because every wire ends in the bus.
- Cache and idempotency outcomes reach the metrics through their observer: the default is both
  the span event and `MetricsObserver`. Queue deliveries are counted where the gateway settles
  them — dead letters rising is the alert, retries rising the warning before it.

### 4.3 Declared — decorate, and it measures

```python
@billing.feature(CommandIssueInvoice)
@metrics.counts(by=of(CommandIssueInvoice).currency)
@metrics.sums(of(ResponseIssueInvoice).total, by=of(CommandIssueInvoice).currency, unit="BOB")
@metrics.measures(of(ResponseIssueInvoice).lines, buckets=(1, 5, 10, 50))
class IssueInvoice(Feature): ...
```

| Decorator | Records on each **successful** run | Name |
|---|---|---|
| `counts(by=)` | one more | `{context}.{use_case}.runs` |
| `sums(value, by=, unit=)` | the field's value, added up (monotonic: a negative value is dropped) | `{context}.{use_case}.{field}` |
| `measures(value, by=, unit=, buckets=)` | the field's value, as a distribution | `{context}.{use_case}.{field}` |

- **Names are derived**: the context from the bus that runs it, the use case from its class
  (`IssueInvoice` → `issue_invoice`), the field from the reference. `counts` ends in `runs`, never
  the bare use case — a field summed under the same use case (`total`) would collide with it on
  Prometheus, where a counter gains `_total`.
- **`of(Dto).field`** is a field reference: `of()` returns a stand-in typed as an instance of the
  DTO, so the type checker and the editor see an ordinary attribute and a rename follows it; at
  run time the stand-in records the path and checks each step against the declared fields. The
  DTO is never modified — no metaclass, no descriptor. Nested paths (`of(R).customer.segment`)
  work.
- **Refused at import**: a field the DTO lacks; a path into a DTO that is neither the use case's
  Command nor its Response (read off `execute`'s annotations); a value that is not a number; a
  label that is not bounded (§4.4); a value handed where a reference is expected.
- **Only successes**: a failed run is already measured, by kind, in §4.2. `declares_metrics(cls)`
  answers whether a use case declared any.

### 4.4 Labels are bounded — normative

A label **MUST** be an `Enum`, a `Literal` or a `bool` (optionally `| None`); anything else is
refused where it is declared. Every distinct value of a label is one more series in the backend
for as long as it lives — a customer id, an amount or free text as a label is how a metrics
backend is taken down. What varies without bound belongs on the trace (a span attribute) or in the
logs. Values travel as the Enum's value, `true`/`false`, or `none`.

### 4.5 By hand, inside `execute`

```python
class PriceOrder(Feature):
    lookups = metrics.counter(by=of(CommandPriceOrder).channel)
    pricing = metrics.timer()

    def execute(self, dto):
        with self.pricing.time():
            self.lookups.add(1, dto)
```

`counter()`, `up_down()`, `histogram()` and `timer()` are declared as class attributes and named
by the attribute (`{context}.{use_case}.{attribute}`); `self.<attribute>` is the instrument bound
to the bus running the use case. Labels are read off the `sources` handed to `add` / `record` /
`time` — each reference from the source it starts at. A `timer` records seconds with the block's
`sincpro.outcome` and `error.type`, a failure as its kind.

### 4.6 Backends — one port, chosen by configuration

`Recorder` is the port: `add(instrument, value, labels)` for counters and up-down counters,
`record(...)` for histograms. The recorder creates its own object for an instrument the first
time it sees its name.

| `OTEL_METRICS_EXPORTER` (alias `SINCPRO_METRICS_BACKEND`, which wins when set) | Recorder |
|---|---|
| not set / `auto` (default) | `OtelRecorder` when a real meter provider or an OTLP endpoint is there; otherwise none |
| `prometheus` | `PrometheusRecorder` on the default registry — `[prometheus]` |
| `otlp` / `otel` | `OtelRecorder` — the host's meter provider, or one installed for OTLP under the process identity (60 s export) |
| `none` / `off`, or `OTEL_SDK_DISABLED=true` | none |

- **Metrics belong to the process**, as a Prometheus registry and an OTel meter provider do: one
  recorder per process, every bus recording into it. `metrics.use(recorder)` replaces it;
  `metrics.using(recorder)` for a block (tests).
- **Prometheus is the reference stack.** Names are translated (dots to `_`, `_total` on a counter,
  `_seconds` on a duration, label keys without dots). `FastApiGateway.app()` serves the scrape at
  `/metrics` when the process records to Prometheus; `asgi_app()` mounts it elsewhere and
  `serve(port)` gives a worker its own. `PROMETHEUS_MULTIPROC_DIR` makes forked workers write to
  one directory the scrape sums (an up-down counter as `livesum`). An instrument redeclared
  under one name with other labels or kind is logged and dropped — Prometheus would raise.
- **`auto` never picks Prometheus by guessing**: a registry nobody scrapes is memory nobody reads.
- **A backend of the project's** implements `Recorder` and passes `RecorderContract`
  (`sincpro_framework.observability.metrics.testing`), the contract every recorder of the framework passes:
  counters add up per label set, up-down counters go both ways, histograms count observations,
  concurrent adds are all kept.

### 4.7 Guarantees

- A recorder that raises is logged once per instrument and skipped; the use case never sees it —
  on the automatic path and inside `execute` alike.
- A counter never goes down.
- With no recorder, a run pays one check: nothing is timed, nothing is read.
- The classic path (a service that only runs buses) imports no DDD layer and no backend for it.

### 4.9 Who a metric comes from

| Level | Where | Value |
|---|---|---|
| the service | the meter provider's resource (`job` on Prometheus) | `service.name` = the artifact **without** its version or registry (`sincpro_odoo_mcp` out of `registry.example.com/sincpro/sincpro_odoo_mcp:0.8.0`); `service.version`; `tenant` = `TENANT` (`resource.tenant`, the canonical key Grafana and the Alloy pipeline read) — only what is set |
| the bounded context | every series | `sincpro.context` |
| what runs each context | `sincpro.context.info` = 1 | `sincpro.context`, `sincpro.artifact`, `sincpro.version`, `sincpro.tenant` |

- **Traces and metrics split the release differently, on purpose.** A trace is looked up by
  release, so its `service.name` is `artifact:version:bus`. A metric is read across releases: a
  version in its job starts every series again at each deploy, breaking `rate()` across it. So
  the metrics resource carries the artifact alone and the version beside it
  (`ObservabilityIdentity.service` / `.service_version`, which split an `APP_RELEASE` that
  arrives whole).
- **Version and tenant travel once**, on the info series — Prometheus' `*_build_info` pattern —
  announced to each recorder the first time a context records into it (so a recorder chosen
  after the bus was built still learns it), and joined in a query with
  `* on (job, instance, sincpro_context) group_left (sincpro_version)`. One series per context:
  never a label that multiplies every series.
- **One source**: the identity and the tenant are the ones traces and errors already use
  (`observability.domain`: `ObservabilityIdentity`, `tenant()`); nothing is resolved twice.

### 4.8 Layers

```
sincpro_framework/observability/metrics/
  domain/          Instrument, InstrumentKind, Declaration, of() / FieldPath and the label and
                   value rules, the naming, the Recorder port — no I/O, no backend
  adapters/        in_memory.py · prometheus.py [prometheus] · otel.py [opentelemetry]
  infrastructure/  the process's recorder (settings), the shield, the per-run measurement,
                   the declarations registry (by class, never on the class)
  entrypoint/      metrics: counts / sums / measures, counter / up_down / histogram / timer
  testing.py       RecorderContract
```

The bus reaches metrics only through `Observability.measure(dto, handler, layer)`, as it reaches
spans through `Observability.span(...)`.

## 5. Errors

- **GlitchTip / Sentry on the framework's own client**, never `sentry_sdk.init()` — calling it
  would replace the host's client and the host's errors would start reporting under sincpro's
  release. Built with `SENTRY_PYTHON_DSN`; one client per release.
- **Each unexpected failure is sent once**, by the handler that raised it, tagged
  `sincpro.kind`, `sincpro.layer`, `sincpro.instance`, `sincpro.dto`, `sincpro.package`, the
  handler and the tenant — and with the keys every signal shares: `service_name`,
  `sincpro.version`, `sincpro.context`, `sincpro.use_case`, `sincpro.outcome`, `error.type` and
  the active span's `trace_id` (also the event's trace context, so the issue opens its trace).
  Its details (DTO chain, where it failed, the execution's context) are the event's `sincpro`
  context. `release` and `environment` are unchanged: the release as deployed, the tenant as
  environment.
- **Expected errors are traffic, not bugs**: `bus.ignore_sentry_exceptions(...)` keeps them out
  of GlitchTip and logs them at info. The host may still capture the same exception under its
  own release — intended.

## 6. Logs

- `sincpro_log`, one `LoggerProxy` per bus shared by its inner buses, so ids bound once reach
  every internal line.
- Every line of a bus carries who it comes from — `service_name`, `service_version`, `tenant` —
  beside `app_name` (kept), once the bus is built; a key the application puts in the execution's
  context wins over them. Every line inside an execution carries its coordinates —
  `sincpro_context`, `sincpro_use_case`, `sincpro_layer` — with or without a tracer, and the
  active span's `trace_id` / `span_id` when a provider is active. The keys are the metric labels'
  names: one filter reads the same on Loki and Prometheus.
- The failure line adds `sincpro_outcome` and the ids of the span it failed in — logged after
  that span ended, it still points at it. Without a provider, only a `with_trace()` block has
  ids (UUIDs).
- How these ids join the other signals — exemplars, trace ↔ logs, GlitchTip, many releases — is
  [correlation](../observability/correlation.md).
- **A failure is logged once**, by the outermost bus, however many ApplicationServices or other
  contexts' buses it crossed: `failed_in` (the bus whose handler raised), `handler`, `layer`,
  `chain`, `error_type`, `error_at` (the last line of the handler's own package), `raised_at`,
  the execution's context. An expected error is logged at info without a traceback; one an
  error handler answered, at warning.
- The transport's own loggers (uvicorn, access logs) learn the request's ids through
  `process.bind_logger(logger)`, and `process.was_reported(error)` keeps a transport from logging
  twice what a bus already logged.

## 7. Configuration

| Setting | Env | Default | Signal |
|---|---|---|---|
| `otlp_endpoint` | `OTEL_EXPORTER_OTLP_ENDPOINT` | none | traces; metrics under `auto`/`otel` |
| `otlp_traces_sample_rate` | `OTEL_TRACES_SAMPLER_ARG` | `1.0` | traces |
| `sentry_dsn` | `SENTRY_PYTHON_DSN` | none | errors |
| `app_release` | `APP_RELEASE` | none | identity |
| `otel_service_name` | `OTEL_SERVICE_NAME` | none | identity, when `APP_RELEASE` is absent |
| `tenant` | `TENANT` | none | every signal: resource `tenant` (traces, metrics), log field, GlitchTip tag and environment |
| `otel_metrics_exporter` | `OTEL_METRICS_EXPORTER` | none | metrics: `otlp`, `prometheus`, `none` |
| `otel_traces_exporter` | `OTEL_TRACES_EXPORTER` | none | traces: `none` builds no provider of the framework's (a host's is still ridden) |
| `otel_sdk_disabled` | `OTEL_SDK_DISABLED` | `false` | traces and metrics off |
| `metrics_backend` | `SINCPRO_METRICS_BACKEND` | `auto` | metrics — alias, wins when set |
| — | `PROMETHEUS_MULTIPROC_DIR` | none | metrics, several worker processes |

Sincpro's reference deployment: `SINCPRO_METRICS_BACKEND=auto` and `OTEL_EXPORTER_OTLP_ENDPOINT`
at the cluster's Alloy — traces and metrics over the same OTLP. Alloy sends traces to Tempo and
metrics to Prometheus (`otelcol.exporter.prometheus` → `prometheus.remote_write`), so a service
needs no scrape target, no ServiceMonitor and no `PROMETHEUS_MULTIPROC_DIR`: each worker process
is its own `instance`. Errors to GlitchTip. `SINCPRO_METRICS_BACKEND=prometheus` with `/metrics`
scraped stays the choice for a host with no collector.

## 8. Decisions

| Decision | Why |
|---|---|
| No enable/disable flags | an extra plus a destination is the switch; a flag is one more thing out of step with the destination |
| A provider per bus, a separate one for the process | a span exported under another context's name is worse than none; transport spans belong to the process |
| Metrics recorded by the bus, not derived from spans | a service without a collector still has them; histograms are exact, not sampled |
| One process recorder, not one per bus | a registry and a meter provider are process-wide in every backend; the context is a label and a name prefix |
| Field references (`of()`), not strings or `Annotated` on the DTO | a rename follows them, a typo fails at import, and the DTO stays untouched |
| Bounded labels enforced, not advised | the one mistake that takes the backend down is refused where it is written |
| `counts` named `…runs` | a summed field named `total` collides with a bare counter on Prometheus |
| Only successes on declared metrics | a failure is already measured by kind; counting it again double-books it |
| OTLP push through Alloy as the reference, the Prometheus adapter for hosts without a collector | every service already exports traces to Alloy: one endpoint, no scrape target per service, prefork workers need no shared directory — and both adapters pass one contract |
| The version off the metrics' service name, onto one info series | a version in the job restarts every series at each deploy; the info series is joined only when a query asks |
| Durations carry seconds buckets (OTel's HTTP ones) | OTel's default buckets are sized for milliseconds and would put every run in the first |

## 9. Conformance — what the tests prove

| Area | Tests |
|---|---|
| traces | `tests/observability/`: spans per DTO and their parentage, adoption of an outer span, W3C carriers, a failure recorded once, the host's provider respected |
| automatic metrics | every run timed with its context and outcome; a failure as its kind, the declared kind (`not_found`) and the idempotency refusals included; what the bus expects as `expected`, in a use case and in a timed block; an answered failure still a failure (global and Feature handlers); an ApplicationService and its Features apart; cache, idempotency and queue outcomes counted |
| declared metrics | counts/sums/measures per label; only successes; the refusals (unknown field, unbounded label, non-number, foreign DTO, value for a reference); a counter never goes down |
| by hand | named by attribute; a timed block's outcome; labels read off the right source |
| safety | a raising recorder never fails a use case (automatic and by hand); no recorder, no cost; the core and the classic path import no backend |
| identity | the info series once per context and recorder, with library, version and tenant; the release split into a stable name and a version; nothing invented without a tenant |
| backends | `RecorderContract` over in-memory, Prometheus and OTel; durations in seconds buckets on OTel; Prometheus names in a real scrape; `/metrics` served by `FastApiGateway`; OTel data points; the configuration's choice, `auto` never guessing Prometheus |

Every rule above has a mutation the tests catch (17 in the metrics pass).

## 10. Not built

- Exemplars on the Prometheus recorder. On OTel they exist without framework code: the SDK's
  `trace_based` filter attaches the DTO span's ids to each point, because the run is measured
  while its span is active; Alloy forwards them (see [correlation](../observability/correlation.md)).
- `service.name` on declared metrics (`counts`, `sums`, `measures`, the instruments by hand):
  their name already carries the context, and `sincpro_context_info` joins the library; only
  `sincpro.use_case.duration` carries the label.
- An expected error's span without the ERROR status — its `sincpro.outcome` already says
  `expected`.
- A log format chosen apart from the level (JSON at `DEBUG`).
- Asynchronous gauges (a callback read at scrape time) — for values that are read, not measured
  (a pool's size).
- Per-wire request metrics (`http.server.request.duration`, `rpc.server.duration`) — the use case
  metric covers every wire already; the host's OTel instrumentation adds these when installed.
- Baggage propagation; a sampling strategy other than parent-based ratio.
