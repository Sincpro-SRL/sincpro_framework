# Observability: tracing and errors

The bus always instruments itself. Extras and environment variables only decide **where** the
data goes: nowhere, an OpenTelemetry collector, a Sentry or GlitchTip DSN.

| Page | What it answers |
|---|---|
| Root README, [Observability](../../README.md#observability) | What works with no extra, what `[opentelemetry]` and `[sentry]` add, the OTLP exporter, `with_trace()`, span attributes, embedding inside an already instrumented host such as Odoo |
| [PRD 03, observability and tracing](../prd/PRD_03_observability-tracing.md) | Why it was built this way |
| [Persistence reference, Observability](../persistence/reference.md#observability) | What the SQLAlchemy adapter reports: every statement to the logger, a span per statement when collecting, every failure to the tracker, never a parameter value |

The two doors in code, `sincpro_framework.observability`:

- `Observability`, one per bus: spans per DTO, errors to GlitchTip, trace ids on its logs.
- `process`, the transport around the buses: the ASGI request, the httpx call, the server's own
  loggers, a database statement, without borrowing a bus's identity.
