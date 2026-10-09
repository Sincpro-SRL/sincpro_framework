# Traces, errors and logs

The long form, in the framework repo: `docs/observability/README.md`,
`docs/observability/span-attributes.md`, `docs/core/interceptors.md` (error handlers), PRD_03.

## What runs with no extra

- **A span per DTO** when `[opentelemetry]` is installed and `OTEL_EXPORTER_OTLP_ENDPOINT` is set
  (or a host already registered a real global provider, which the bus then rides); otherwise a
  no-op. `OTEL_TRACES_EXPORTER=none` or `OTEL_SDK_DISABLED=true` turns it off;
  `OTEL_TRACES_SAMPLER_ARG` (0–1) samples the traces the bus starts. The span is named `context/DTO` and carries `sincpro.context`,
  `sincpro.use_case`, `sincpro.layer`, `sincpro.instance`, `sincpro.outcome`, `error.type`.
- **Each failure once** to Sentry/GlitchTip when `[sentry]` is installed and `SENTRY_PYTHON_DSN` is
  set (an `http(s)://…@…` URL; anything else is off). The client is isolated — it does not call
  `sentry_sdk.init()` and does not reuse the host's (Odoo's).
- **Logs correlated**: every line carries `trace_id` and `span_id`, plus the identity keys and the
  execution context.
- **Metrics** (see `metrics.md`).

Traces, errors and metrics are **independent**: enable any subset.

## Adopting an incoming trace

```python
with bus.with_trace(carrier=headers) as traced:      # {"traceparent": "...", "tracestate": "..."}
    traced(Command(...), Response)

with bus.with_parent_trace() as traced:              # the host's active span is the parent
    traced(Command(...), Response)
```

Both build the bus if it is not built yet — register interceptors, dependencies and `auth.on(bus)`
first.

The entrypoints do this by themselves (JSON-RPC `context`, gRPC metadata, a queue's CloudEvents
headers). A host with its own OpenTelemetry (`FastAPI`, Odoo) opens the parent span; the bus span is
its child.

## Business fields on the span

```python
from sincpro_framework.observability import of, traces

@billing.feature(CommandIssueInvoice)
@traces.attributes(
    of(CommandIssueInvoice).customer_nit,              # billing.customer_nit, before execute runs
    of(ResponseIssueInvoice).reception_code,           # billing.reception_code, on success
    namespace="billing",                               # required: the prefix every declared key takes
    channel=of(BillingContext)["channel"],             # billing.channel, read off the execution context
)
class IssueInvoice(Feature):
    def execute(self, dto: CommandIssueInvoice) -> ResponseIssueInvoice:
        traces.annotate({"billing.document": "F-1"})   # a value known only midway, key as written
        ...
```

Both write on the span the bus opened for the use case, never on a child an adapter opened. A
declaration is checked at import for what could never work (a missing field, a value a span cannot
hold, a key declared twice); by hand, a value a span cannot hold is dropped and logged once.
Outside a use case, or without OpenTelemetry, both do nothing.

Any key goes on the span — nothing is refused for its name; a key the framework also writes takes
the project's value. The execution context is on the span by itself (every key, plus `tenant` and
`user_id`), so a value every signal should carry goes in `self.context[...]`, not in `annotate`.
Tempo filters and groups by span attributes.

## Error handling by layer

Three nested scopes — **feature** (innermost), **app service** (only when the Feature runs
through an ApplicationService), **global** (the bus, outermost). Within a scope the first
registered runs first; a re-raise passes to the next handler, then to the next scope out.

```python
framework.add_global_error_handler(handler)
framework.add_feature_error_handler(handler)
framework.add_app_service_error_handler(handler)
```

Handlers can be registered before or after the first execution. **What a handler returns becomes the
bus's answer** — a handler written only to watch returns `None` and silently swallows the failure.
Re-raise (`raise error`) to delegate. `bus.ignore_sentry_exceptions(SomeError)` marks expected
traffic so it is not reported and its metric outcome is `expected`.

An error handler takes the same `replaces=`, `before=`, `after=`, `sequence=` as an interceptor.

## Embedding in an already-instrumented host

Inside Odoo (or any host with its own OTel/Sentry), the SDK keeps **its own name and version** on
the signals, and the framework does not re-init the host's client. `SINCPRO_FRAMEWORK_LOG_LEVEL`
controls the framework's own log lines (`INFO` keeps the debug SQL out).

## What the framework itself sends

SQL travels without its values (`INSERT INTO thing (…) VALUES (?, ?, ?)`); the framework's error
report carries names, never values; an internal failure's text never reaches a wire
(`said_to_the_caller`). What a project adds — context keys, span attributes, log fields — goes as
written: the choice is the project's, and `hide_in_logs` keeps a key off every signal.
