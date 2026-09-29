# Observability: traces, errors, logs and metrics

The bus always instruments itself. Extras and environment variables only decide **where** the
data goes: nowhere, an OpenTelemetry collector, a Sentry or GlitchTip DSN, Prometheus.

| Signal | Recorded | Extra | Destination |
|---|---|---|---|
| Logs with `trace_id` / `span_id` | always | none | stdout / your logger |
| Traces — a span per DTO | always | `[opentelemetry]` | `OTEL_EXPORTER_OTLP_ENDPOINT` (Tempo, Jaeger) |
| Errors — each failure once | always | `[sentry]` | `SENTRY_PYTHON_DSN` (GlitchTip, Sentry) |
| Metrics — every use case, plus what it declares | always | `[prometheus]` or `[opentelemetry]` | `SINCPRO_METRICS_BACKEND` (`/metrics`, OTLP) |

| Page | What it answers |
|---|---|
| [Metrics](metrics.md) | what every use case records by itself, decorating a use case to count, sum or measure a field, instruments inside `execute`, Prometheus and OpenTelemetry |
| Root README, [Observability](../../README.md#observability) | what works with no extra, what `[opentelemetry]` and `[sentry]` add, `with_trace()`, span attributes, embedding inside an already instrumented host such as Odoo |
| [PRD 03, observability](../prd/PRD_03_observability-tracing.md) | the design and why — every signal |
| [Persistence reference, Observability](../persistence/reference.md#observability) | what the SQLAlchemy adapter reports: every statement to the logger, a span per statement when collecting, every failure to the tracker, never a parameter value |

The doors in code:

- `sincpro_framework.observability.Observability`, one per bus: spans per DTO, errors to
  GlitchTip, trace ids on its logs, the metrics of each run.
- `sincpro_framework.observability.process`, the transport around the buses: the ASGI request,
  the httpx call, the server's own loggers, without borrowing a bus's identity.
- `sincpro_framework.observability.metrics`: `metrics` (declare, pick the recorder) and `of` (field
  references).
