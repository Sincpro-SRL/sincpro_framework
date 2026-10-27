# Observability: traces, errors, logs and metrics

The bus always instruments itself. Extras and environment variables only decide **where** the
data goes: nowhere, an OpenTelemetry collector, a Sentry or GlitchTip DSN, Prometheus.

| Signal | Recorded | Extra | Destination |
|---|---|---|---|
| Logs with `trace_id` / `span_id` | always | none | stdout / your logger |
| Traces — a span per DTO | always | `[opentelemetry]` | `OTEL_EXPORTER_OTLP_ENDPOINT` (Tempo, Jaeger) |
| Errors — each failure once | always | `[sentry]` | `SENTRY_PYTHON_DSN` (GlitchTip, Sentry) |
| Metrics — every use case, plus what it declares | always | `[prometheus]` or `[opentelemetry]` | `OTEL_METRICS_EXPORTER` (OTLP, `/metrics`) |

| Page | What it answers |
|---|---|
| [Correlation](correlation.md) | how one failure is followed across metrics, traces, logs and GlitchTip; what the framework emits for it, what the stack must be told, many customers and releases, on-premise |
| [Span attributes](span-attributes.md) | what a use case puts on its own span — a NIT, a merchant, a provider's code — declared with `field_path()` or by hand; the rules on keys and values; filtering and grouping by them in Tempo without a series per value |
| [Metrics](metrics.md) | what every use case records by itself, decorating a use case to count, sum or measure a field, instruments inside `execute`, Prometheus and OpenTelemetry |
| Root README, [Observability](../../README.md#observability) | what works with no extra, what `[opentelemetry]` and `[sentry]` add, `with_trace()`, span attributes, embedding inside an already instrumented host such as Odoo |
| [PRD 03, observability](../prd/PRD_03_observability-tracing.md) | the design and why — every signal |
| [Persistence reference, Observability](../persistence/reference.md#observability) | what the SQLAlchemy adapter reports: every statement to the logger, a span per statement when collecting, every failure to the tracker, never a parameter value |

The doors in code:

- `sincpro_framework.observability.Observability`, one per bus: spans per DTO, errors to
  GlitchTip, trace ids on its logs, the metrics of each run.
- `sincpro_framework.observability.process`, the transport around the buses: the ASGI request,
  the httpx call, the server's own loggers, without borrowing a bus's identity.
- `sincpro_framework.observability.http`, the HTTP host in one line each: `http_middleware(app)`
  and `configure_process_logging(level)` — below.
- `sincpro_framework.observability.metrics`: `metrics` (declare, pick the recorder) and `field_path` (field
  references) — the one door of `field_path`, the span attributes take it from there too.
- `sincpro_framework.observability.traces`: what a use case says on its own span —
  `@traces.attributes(...)` with the same `field_path`, and `traces.annotate({...})` inside `execute`.

## An HTTP host: one line and one span per request

What never reaches a use case — a 401 from the host's own auth, a 413 from a body limit, a route
that does not exist — is seen only by the host. Two lines give it what the buses already have:

```python
from sincpro_framework.observability.http import configure_process_logging, http_middleware

configure_process_logging("INFO")   # uvicorn, httpx, starlette: the framework's format, trace ids
app = http_middleware(app)          # any ASGI app — FastAPI, Starlette, FastMCP's
```

- **One line per request**: method, path, status, duration and the size the request declared
  (`request_bytes`, what explains a 413), with `trace_id` — never the query string, where a
  credential may ride. `info`, `warning` from 400, `error` from 500; a path probes hit
  (`quiet_paths`, `/health` by default) only when it fails, and never traced.
- **A refusal says what the host wants said**: `describe=(scope, status, body_head) -> fields`
  adds fields of its own to a line from 400 up, with the first `body_bytes` of the answer — what a
  transport answered, a client header; a description that fails never fails the line.
- **What escaped before answering** is written with its traceback and reported to the error
  tracker (`process.record_error(..., layer="asgi")`).
- **One server span per request** with `[opentelemetry]`: the child of the request's
  `traceparent`, or a root of its own behind a gateway that starts traces
  (`trust_incoming_trace=False`); the buses' spans and every log line of the request land on it.
- **The libraries' logging** through the root, in the framework's format and where it writes —
  stdout or its file; with the `stdlib` backend (inside Odoo) the root stays the host's and the
  libraries only propagate to it. httpx is traced when `opentelemetry-instrumentation-httpx` is
  installed — `instrument_httpx()` on its own, once however many times it is called, for a host
  that configures logging before its buses are built.

