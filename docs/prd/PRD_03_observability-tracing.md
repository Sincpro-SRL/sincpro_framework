# PRD_03: Observability — traces, errors, logs and metrics

- **Status**: built. Traces, errors and logs since the observability refactor; metrics (§4)
  automatic, declared and by hand, on Prometheus (the reference stack) or OpenTelemetry; what a
  use case says on its own span (§3.1) built with this revision. The execution context on
  every signal (§4.10, a normative rule): `release`, `service_name`, `service_version` and the
  execution's `tenant` on logs, spans, every metric series and GlitchTip; every context key on the
  span and in GlitchTip; the metric labels a bus declares; `of(ContextType)["key"]`; no refusal on
  content. What is left is §10.
- **Extras**: none for the core. `[opentelemetry]` (traces, OTLP metrics), `[sentry]` (errors to
  GlitchTip/Sentry), `[prometheus]` (metrics scraped at `/metrics`).
- **Code**: `sincpro_framework.observability` (the two doors: `Observability` per bus,
  `process` for the transport; and `traces`, a use case's span attributes) and
  `sincpro_framework.observability.metrics`.
- **Guides**: [observability](../observability/README.md), [metrics](../observability/metrics.md),
  [span attributes](../observability/span-attributes.md).

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

### 3.1 What a use case says about itself

**Why.** The span of a use case said who ran it and how it ended, never *what it was about*. The
only way to add to it was `Observability.annotate(span, attrs)`, internal and asking for a span
object no use case holds. So the questions production asks went unanswered, and each consumer
started patching around it:

- **sincpro-siat-soap** (SIAT invoicing inside Odoo, many companies per database) needs, on each
  send and each annulment, the issuing NIT, the branch and point of sale, SIAT's reception code
  and the CUF. They vary without bound, so a metric refuses them as labels (§4.4) — rightly — and
  they ended up nowhere.
- **sincpro-payments-sdk** needs the merchant, the QR id and the bank's transaction id.
- **sincpro_mcp_odoo** wrote its own helper in its transport (`tool_call_span.py`), writing on
  whatever span happened to be active — the right idea, outside the framework, and on the wrong
  span as soon as the call reaches a bus.

**What it gives.** Filtering in Tempo by what the use case was about — "the invoices of NIT X
that failed in the last hour"; grouping by it with TraceQL metrics
(`{span.sincpro.use_case="CmdSendDocumentToSiat"} | count_over_time() by (span.siat.nit)`), which is
use per company **without one Prometheus series per company**; the jump from a log line or an
alert (its `trace_id`, [correlation](../observability/correlation.md)) to the exact run and what it
carried; the audit of one rejection — who sent it, from where, what SIAT answered.

**What it is not.** Not a metric: nothing is counted or summed, nothing is kept once the trace
ages out, and a TraceQL metric reads only the sampled traces — for looking, never for billing. It
does not replace `@metrics.*`: what a dashboard plots for months is a bounded metric; what a person
has to find is a span attribute.

**How.** `sincpro_framework.observability.traces`, two doors onto one span — the one the bus opened
for the use case, `context/Command`:

```python
@siat_soap_sdk.app_service(CmdSendDocumentToSiat)
@traces.attributes(
    of(CmdSendDocumentToSiat).nit,                      # siat.nit
    of(ResSendDocumentToSiat).reception_code,           # siat.reception_code
    namespace="siat",
    branch=of(CmdSendDocumentToSiat).branch_office,     # siat.branch
    point_of_sale=of(CmdSendDocumentToSiat).point_of_sale,
)
class SendDocumentToSiat(ApplicationService):
    def execute(self, dto: CmdSendDocumentToSiat) -> ResSendDocumentToSiat:
        received = self.feature_bus.execute(..., ResSendDocumentToSiat)   # its own span
        traces.annotate({"siat.status": received.literal_status})         # back on this one
        ...
```

- **Declared** — `@traces.attributes(*of(X).field, namespace=, **name=of(X).field)`, the `of()`
  of the metrics (§4.3), checked where it is written. A reference is keyed `{namespace}.{path}`, a
  named one `{namespace}.{name}`. What starts at the **Command** is set before `execute` runs, so a
  failed run carries it — the failed runs are the ones someone looks for; what starts at the
  **Response**, after a success.
- **By hand** — `traces.annotate({key: value})`, for a value known only midway (a code the
  provider answered, an id computed in `execute`), from a Feature, an ApplicationService or an
  interceptor.
- **Which span.** The bus keeps the use case's span in a context variable while it runs;
  both doors write there, never on OTel's *active* span — an adapter's HTTP or SQL span would
  otherwise take the attribute. An ApplicationService's attributes stay on its span; each Feature
  it runs keeps its own; after `self.feature_bus.execute(...)` returns, the use case running is the
  ApplicationService again. `thread_context()` and the async bus carry the variable.

**Rules — normative.** Nothing is refused for its name or its content (§4.10 rule 8): what a
service shows is its decision.

1. A key is any text; `{namespace}.{field}` for a declared one. The domain first reads best
   (`siat.nit`, `payment.merchant_id`). A key the framework also writes (`sincpro.outcome`) takes
   the project's value.
2. A value is a `str`, `bool`, `int`, `float` or a homogeneous sequence of them; an `Enum` travels
   as its value, a `Decimal` as a float, a `UUID` as text, a `date` as ISO 8601, a mixed sequence as
   text; `None` stays off the span. A DTO, a mapping, `bytes` or `Any` do not fit a span.
3. The execution context goes on the span by itself (§4.10 rule 2); a reference into the context
   type reads it (`of(BillingContext)["user_id"]`, §4.10 rule 6).
4. **Declared: refused at import** (`ContractViolation`) only for what could never work — a field
   the DTO or the context type lacks, a path into a DTO or a context that is not the use case's, a
   value that does not fit a span, a key declared twice, a declaration with nothing in it, an
   empty namespace.
5. **By hand: never raises.** A value a span cannot hold is dropped and logged once; the rest are
   set; the use case runs (principle 2).
6. Without `[opentelemetry]`, with `OTEL_SDK_DISABLED`, or with no endpoint and no host provider,
   both doors do nothing; a declared use case pays one check per run. Outside a use case,
   `annotate` does nothing.

**Alternatives considered.**

| Option | For | Against | Taken |
|---|---|---|---|
| Declarative only, `@traces.attributes(of(Command).nit)` | the metrics' `of()`: a rename follows it, a typo or an unfit type fails at import; the Command's attributes are on the span even when `execute` raises before any line of it could; no code in `execute` | a value known only midway (a provider's code, a computed CUF) cannot be declared | as the default |
| Imperative only, `self.trace.annotate({...})` | covers anything | keys are strings nobody checks until run time; the author must remember to write it before whatever may raise; a name on `Feature` shadows a dependency called `trace`, and a Feature instance is shared by concurrent runs, so it cannot hold the span | no |
| Both | the declaration for what the Command and Response carry, `traces.annotate` for what appears midway | two ways to do one thing | **yes** — the declaration first, by hand only for what it cannot say |
| OTel's active span (`trace.get_current_span()`), as `tool_call_span.py` does | no framework code | lands on whatever child an adapter opened — the attribute is not where the use case's span is filtered | no |

`traces.annotate` is a module-level door, not `self.trace`: it works the same in a Feature, an
ApplicationService and an interceptor, needs nothing from the instance, and never collides with a
dependency's name.

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
- **Refused at import**: a field the DTO or the context type lacks; a path into a DTO that is
  neither the use case's Command nor its Response (read off `execute`'s annotations), or into a
  context type it does not declare; a value that is not a number; a value handed where a reference
  is expected. A label that is not bounded is warned about, not refused (§4.4).
- **Only successes**: a failed run is already measured, by kind, in §4.2. `declares_metrics(cls)`
  answers whether a use case declared any.

### 4.4 Labels: the project's choice, warned when unbounded — normative

A label is what the project chooses. One that is not an `Enum`, a `Literal` or a `bool`
(optionally `| None`) logs one warning where it is declared, saying that every distinct value is
one more series in the backend for as long as it lives, and is accepted (§4.10 rule 8). A value
that varies without bound — a customer id, an amount — usually reads better on the trace (a span
attribute, §3.1) or in the logs. Values travel as the Enum's value, `true`/`false`, or `none`.

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

> Version and tenant are also on every series (§4.10): the info series stays, and is no longer the
> only place a metric says them.

### 4.10 Correlation: the execution context on every signal — normative

- **Status**: built. A rule of the spec: every signal of every bus obeys it. It replaces §3.1
  rules 1 and 3 and §4.4 where they refused, and §4.9 where it kept version and tenant off the
  series.
- **Code**: `observability/correlation.py` (what every signal reads), `context/infrastructure/tree.py` (the
  live context of the execution), `metrics/infrastructure/active.py` (`correlated`, where every
  measurement passes), `errors/record_error.py`, `api.py` (`Observability.span`).

#### The rule

1. **Four keys, always, on the four signals. The framework puts them there**, never the
   collector, the orchestrator or the deploy.

   | Key | Value | Logs | Span | Metric series | GlitchTip |
   |---|---|---|---|---|---|
   | `release` | `APP_RELEASE` verbatim, else `artifact:version` (`ObservabilityIdentity.release`) | `release` | resource `release` | label `release` | the release, and tag `release` |
   | `service_name` | the artifact, stable across releases | `service_name` | resource `service.name` | label `service.name` (Prometheus `service_name`) | tag `service_name` |
   | `service_version` | the version | `service_version` | resource `service.version` | label `service.version` (Prometheus `service_version`) | tags `service_version`, `sincpro.version` |
   | `tenant` | the execution's (rule 3) | `tenant` | attribute `tenant`; resource `tenant` = the deployment's | label `tenant` | tag `tenant`; `environment` = the deployment's |

   `release` is there so one filter finds a deployed artifact exactly as it was shipped
   (`registry…/sincpro_odoo_mcp:0.8.0`), on every tool; `service_name` + `service_version` is the
   same thing split up. An unknown value is left off — on Prometheus, whose series have fixed
   label keys, it is empty, which Prometheus reads as absent.

2. **The execution context is the source.** It is the framework's language: the entrypoint, the
   application (`bus.context({...})`), an interceptor or a hook, and the handler
   (`self.context`) all write to it. **Every key of the context goes on every signal of the
   execution, with nothing to declare**:
   - on each log line;
   - as an attribute of the DTO span;
   - as a GlitchTip tag (its text, cut at 200 characters);
   - as a metric label when the bus declares it (rule 5).

   The only filter is the one the project chooses: `hide_in_logs` keeps a key off **every**
   signal. The trace's own plumbing (`trace_id`, `span_id`, `carrier`) is not copied onto the span
   or the event, which hold it natively. Nothing else is left out.

3. **The tenant and the user.**
   - The execution's tenant is the context's `tenant`; else the authenticated identity's
     (`current_identity().tenant`); else the deployment's: `TENANT`, then the `tenant` key of
     `OTEL_RESOURCE_ATTRIBUTES` (comma-separated `key=value`, percent-decoded, stripped).
   - The execution's user is the context's `user_id`; else the authenticated identity's
     `subject`. It goes to GlitchTip as the event's user too (`scope.set_user({"id": ...})`), so
     every issue says how many users it hit.
   - The identity is read by observability itself, so every entrypoint that authenticates — REST,
     JSON-RPC, gRPC, FastAPI, MCP, a queue, remote execution, `as_identity` by hand — gives it
     with no code of its own. The context wins over it: the application says for whom it works,
     and every signal follows. A service that never loads auth pays nothing for it.

4. **A value counts from the moment it is set.** A log line reads the context when it is
   written. The span, the metric and the GlitchTip event read it when they are recorded — for the
   span and the metric, at the end of the use case. So a key an interceptor, a hook or `execute`
   sets counts on all of them (interceptors run inside the span: `bus.py`), and so does one said
   in a `bus.context(...)` scope that closed before the use case did: the execution remembers
   what the scopes opened inside it said (`correlation.remember`). A nested bus and
   `thread_context()` inherit the context. A queue message hands its `correlationid` and a
   `tenant` header to it (`queue_context`). A cron serving many tenants runs each under
   `bus.context({"tenant": t})`.

5. **Metric labels from the context are declared before the build, because the backends need
   it.** Prometheus and OpenTelemetry create each instrument with a fixed set of label keys, and
   a series cannot gain a label afterwards. So the context keys that go on **every** series are
   named up front:

   ```python
   billing = UseFramework("billing", metric_labels=(of(BillingContext)["company"], "channel"))
   ```
   ```yaml
   metric_labels: [company, channel]
   ```

   They are the process's: every series of every bus carries them. `correlated`
   (`metrics/infrastructure/active.py`), which every measurement passes, adds the four keys of
   rule 1 and the declared ones to the instrument's label keys and to the labels — so
   `sincpro.use_case.duration`, `counts` / `sums` / `measures`, the instruments by hand,
   `sincpro.cache.outcomes`, `sincpro.idempotency.outcomes` and `sincpro.queue.deliveries` carry
   them alike, on the in-memory, Prometheus and OTel recorders, with no collector. What the
   measurement names itself wins. `sincpro.context.info` describes the process and is not
   correlated (`Instrument.correlated=False`). A Prometheus metric that meets a wider set of
   labels than it was created with keeps its own, and says so once.

6. **A typed context, referenced like a DTO: `of(ContextType)["key"]`.** A bounded context
   types its context as a `TypedDict`: `Feature[Command, Response, BillingContext]`,
   `ApplicationService[..., BillingContext]`, `Hook[BillingContext]`. The same type is a root for
   every field reference, read by key as the context itself is:

   ```python
   class BillingContext(TypedDict, total=False):
       tenant: str
       company: Company        # Enum
       channel: Channel        # Enum
       user_id: str

   @billing.app_service(CommandIssueInvoice)
   @metrics.counts(by=(of(CommandIssueInvoice).currency, of(BillingContext)["channel"]))
   @traces.attributes(of(BillingContext)["user_id"], of(CommandIssueInvoice).nit, namespace="billing")
   class IssueInvoice(BillingService): ...
   ```

   - `of()` reads a `TypedDict`'s keys: one the context type lacks fails at import, as a field a
     DTO lacks does. `of(ContextType)` is typed as a mapping, so a `total=False` context raises
     no complaint from the type checker; the key is checked where it is written.
   - A path may start at the handler's Command, its Response, or its `ContextT` (read off its
     generic parameters); one into another context type is refused. In `metric_labels`, any
     context type.
   - At run time a context path reads the execution's context when the signal is recorded
     (rule 4): on the span at the start, at the end and on a failure; on a metric when it is
     recorded. A missing key gives no value.

7. **What a use case adds, with decorators and by hand.** `@traces.attributes` and
   `traces.annotate` put fields on the span (§3.1); `@metrics.counts/sums/measures(by=...)` on a
   metric (§4.3). Both accept context paths (rule 6). A value that should be on every signal of
   the execution goes in the context: `self.context["siat_cuf"] = cuf`. No other API is needed.

8. **No refusal on content.** The framework gives tools; it restricts nothing:
   - no key is refused for its name — no list of words that name a secret, no reserved
     namespace. A key the framework also writes takes the project's value;
   - a metric label that is not an `Enum`, a `Literal` or a `bool` logs one warning where it is
     declared, saying that each distinct value is one more series, and is accepted;
   - what stays is what could never work: a field reference that does not exist, a value a span
     cannot hold (on the context's own keys it travels as its text instead), a key declared
     twice, a declaration with nothing in it.

9. **What exists keeps working.** The resource describes the process (`service.name`,
   `service.version`, `release`, the deployment's `tenant`). `sincpro_context_info` keeps naming
   the library behind each context. Adding labels keeps every selector written today matching,
   and the `group_left (sincpro_version)` join keeps working. Alloy's `metrics_tenant` sets the
   label only when absent, so it is a no-op for framework metrics. On Tempo `span.tenant` is the
   execution's and `resource.tenant` the deployment's.

**Why the old objections do not hold.** A version on the series "restarts every series at each
deploy", but each series already carries `instance`, a new UUID on every start. The tenant and
the user were kept off for safety; what a service shows is the service's decision, and
`hide_in_logs` is the tool to hide it.

**Not here (§10).** W3C `baggage`, to carry the context to a service that does not use the
framework. Between Sincpro services the context already crosses (remote execution, gRPC
`sp-ctx-*`).

#### Conformance

`tests/observability/test_execution_correlation.py`, beside `test_correlation_contract.py`:

- `release`, `service_name`, `service_version` and `tenant` on the log line, **the metric**, the
  span and the GlitchTip event of one failure.
- A tenant only in `OTEL_RESOURCE_ATTRIBUTES` on all four and in the GlitchTip environment; no
  tenant anywhere gives no `tenant` key at all.
- One process, two executions under `{"tenant": "acme"}` and `{"tenant": "bo"}`: each its own on
  the four signals; the resource keeps the deployment's.
- Every context key on the span and as a GlitchTip tag, `user_id` as the event's user; a key in
  `hide_in_logs` on none of the four.
- The authenticated identity gives tenant and user when the context has none; the context wins.
- A tenant an interceptor sets in a `bus.context` scope and a key the handler writes midway, on
  the span and the metric.
- A nested bus and a `thread_context()` worker inherit the tenant.
- A metric label declared on the bus, by a field reference or in the settings, on every series,
  and on Prometheus.
- `of(BillingContext)["company"]` in `by=` and `@traces.attributes`; a key the TypedDict lacks and
  another context type refused at import.
- A queue message's `correlationid` and `tenant` header in the context.
- `test_span_attributes.py`: no key refused for its name; `test_use_case_metrics.py`: an
  unbounded label accepted with one warning.

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
| `tenant` | `TENANT`, else `tenant` in `OTEL_RESOURCE_ATTRIBUTES` | none | the deployment's tenant: the resource, the GlitchTip environment, and the default of an execution without one; the execution's tenant comes from its context first (§4.10) |
| `metric_labels` | — | none | context keys on every metric series, beside the four of §4.10 rule 1 (§4.10 rule 5); also `UseFramework(metric_labels=...)` |
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
| A use case's span attributes declared with the metrics' `of()`, plus `traces.annotate` for what appears midway (§3.1) | one reference vocabulary for metrics and traces, checked at import; the Command's attributes on a failed run too; by hand only for what a declaration cannot say |
| Span attributes on the use case's span, kept in a context variable, not on OTel's active span | an adapter's child span would take the attribute and the use case's span — the one filtered by `sincpro.use_case` — would lack it |
| Sensitive key names refused, `sincpro.*` and OTel namespaces reserved | a token on a trace is a leak anyone with Grafana reads; a use case overwriting `sincpro.outcome` lies on every span |
| Field references (`of()`), not strings or `Annotated` on the DTO | a rename follows them, a typo fails at import, and the DTO stays untouched |
| Bounded labels enforced, not advised | the one mistake that takes the backend down is refused where it is written |
| `counts` named `…runs` | a summed field named `total` collides with a bare counter on Prometheus |
| Only successes on declared metrics | a failure is already measured by kind; counting it again double-books it |
| OTLP push through Alloy as the reference, the Prometheus adapter for hosts without a collector | every service already exports traces to Alloy: one endpoint, no scrape target per service, prefork workers need no shared directory — and both adapters pass one contract |
| The version off the metrics' service name, on a label of its own (§4.10) and on the info series | the service name stays stable across releases; `instance` already opens new series at each deploy, so a version label opens none more |
| `service_name`, `service_version` and `tenant` on every signal, the tenant resolved per execution from the context (§4.10) | the same keys on every signal is the promise of correlation; the context is the framework's language, so it replaces the deployment's default everywhere at once; `instance` already opens new series on each deploy, so a version label adds none; a collector copying them is a deployment detail the framework must not depend on |
| Observability reads the authenticated identity's tenant and subject when the context has none; the context always wins | every entrypoint that authenticates gives them with no code of its own; the context is the framework's language, so the application, a hook or the handler may say for whom it works and every signal follows |
| Every context key on logs, span and GlitchTip without declaring it; metric labels declared on the bus; no refusal on content, `hide_in_logs` the only filter | the backends fix a series' labels when the instrument is created, the other signals do not; what a service shows is the service's decision |
| Durations carry seconds buckets (OTel's HTTP ones) | OTel's default buckets are sized for milliseconds and would put every run in the first |

## 9. Conformance — what the tests prove

| Area | Tests |
|---|---|
| traces | `tests/observability/`: spans per DTO and their parentage, adoption of an outer span, W3C carriers, a failure recorded once, the host's provider respected |
| span attributes | `tests/observability/tracing/test_span_attributes.py`: declared (Command and Response) and by hand on the `context/Command` span; a failed run keeps the Command's; an ApplicationService, its Feature and an adapter's child span each keep their own; a value a span cannot hold is dropped by hand and the use case runs; no key refused for its name; outside a use case nothing; every refusal at import. `tests/test_core_without_extras.py`: both doors with OTel blocked |
| automatic metrics | every run timed with its context and outcome; a failure as its kind, the declared kind (`not_found`) and the idempotency refusals included; what the bus expects as `expected`, in a use case and in a timed block; an answered failure still a failure (global and Feature handlers); an ApplicationService and its Features apart; cache, idempotency and queue outcomes counted |
| declared metrics | counts/sums/measures per label; only successes; the refusals (unknown field, non-number, foreign DTO, value for a reference); an unbounded label warned and accepted; a counter never goes down |
| correlation (§4.10) | `tests/observability/test_execution_correlation.py`: the four keys on the four signals; the tenant per execution, from the context, the identity or the deployment; every context key on the span and the event; `hide_in_logs` on all four; a key set midway; declared metric labels; `of(ContextType)["key"]`; the queue's correlation |
| by hand | named by attribute; a timed block's outcome; labels read off the right source |
| safety | a raising recorder never fails a use case (automatic and by hand); no recorder, no cost; the core and the classic path import no backend |
| identity | the info series once per context and recorder, with library, version and tenant; the release split into a stable name and a version; nothing invented without a tenant |
| backends | `RecorderContract` over in-memory, Prometheus and OTel; durations in seconds buckets on OTel; Prometheus names in a real scrape; `/metrics` served by `FastApiGateway`; OTel data points; the configuration's choice, `auto` never guessing Prometheus |

Every rule above has a mutation the tests catch (17 in the metrics pass).

## 10. Not built

- Exemplars on the Prometheus recorder. On OTel they exist without framework code: the SDK's
  `trace_based` filter attaches the DTO span's ids to each point, because the run is measured
  while its span is active; Alloy forwards them (see [correlation](../observability/correlation.md)).
- W3C `baggage` for the execution context, towards services that do not use the framework.
- An expected error's span without the ERROR status — its `sincpro.outcome` already says
  `expected`.
- A log format chosen apart from the level (JSON at `DEBUG`).
- Asynchronous gauges (a callback read at scrape time) — for values that are read, not measured
  (a pool's size).
- Per-wire request metrics (`http.server.request.duration`, `rpc.server.duration`) — the use case
  metric covers every wire already; the host's OTel instrumentation adds these when installed.
- Baggage propagation; a sampling strategy other than parent-based ratio.
