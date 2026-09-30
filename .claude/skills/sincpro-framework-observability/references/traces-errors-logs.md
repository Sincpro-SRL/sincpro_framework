# Traces, errors and logs

Depth: `docs/observability/README.md`, `docs/observability/span-attributes.md`, `docs/core/interceptors.md` (error handlers),
PRD_03.

## What runs with no extra

- **A span per DTO** when `[opentelemetry]` is installed and `OTEL_EXPORTER_OTLP_ENDPOINT` is set;
  otherwise a no-op. The span is named `context/DTO` and carries `sincpro.context`,
  `sincpro.use_case`, `sincpro.layer`, `sincpro.instance`, `sincpro.outcome`, `error.type`.
- **Each failure once** to Sentry/GlitchTip when `[sentry]` is installed and `SENTRY_PYTHON_DSN` is
  set. The client is isolated — it does not call `sentry_sdk.init()`, does not reuse Odoo's.
- **Logs correlated**: every line carries `trace_id` and `span_id`, plus the identity keys.
- **Metrics** (see `metrics.md`).

Traces, errors and metrics are **independent**: enable any subset.

## Adopting an incoming trace

```python
with bus.with_trace(carrier):        # {"traceparent": "...", "tracestate": "..."}
    bus(Command(...), Response)
```

The entrypoints do this by themselves (JSON-RPC `context`, gRPC metadata, a queue's CloudEvents
headers). A host with its own OpenTelemetry (`FastAPI`, Odoo) opens the parent span; the bus span is
its child.

## Business fields on the span

```python
from sincpro_framework.observability import traces

@billing.feature(CommandIssueInvoice)
@traces.attributes(nit=of(CommandIssueInvoice).customer_nit, merchants=of(CommandIssueInvoice).merchants)
class IssueInvoice(Feature):
    def execute(self, dto):
        traces.annotate({"document": "F-1"})
        ...
```

Bounded keys: put a NIT, a provider's code, a merchant on the span; never a value with unbounded
cardinality on a metric. Tempo filters and groups by span attributes.

## Error handling by layer

Three independent scopes — **global** (framework bus), **feature**, **app service**. First
registered runs first; re-raise to delegate to the next.

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

## What never leaves the process

SQL travels without its values (`INSERT INTO thing (…) VALUES (?, ?, ?)`); an error report carries
names, never values; an internal failure's text never reaches a wire (`said_to_the_caller`). Do not
add a value to a log field, a span attribute or an error report that should not be read by whoever
reads the telemetry.
