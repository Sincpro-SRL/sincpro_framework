---
name: sincpro-framework-observability
description: Instrument a sincpro_framework service — traces (OTLP), errors (Sentry/GlitchTip), correlated logs and metrics (Prometheus), with nothing declared for the basics and decorators for business metrics. Use whenever a task adds tracing, error reporting, logging, metrics, span attributes, dashboards, alerting, or asks why a use case has no span/Sentry event.
---

# sincpro-framework-observability

The bus instruments itself. Extras and environment variables only decide **where** the data goes.
Depth: `docs/observability/README.md`, `metrics.md`, `correlation.md`, `span-attributes.md`, PRD_03.

| Signal | Recorded | Extra | Destination |
|---|---|---|---|
| Logs with `trace_id`/`span_id` | always | none | stdout / your logger |
| Traces — a span per DTO | always | `[opentelemetry]` | `OTEL_EXPORTER_OTLP_ENDPOINT` |
| Errors — each failure once | always | `[sentry]` | `SENTRY_PYTHON_DSN` |
| Metrics — every use case + declared | always | `[prometheus]` / `[opentelemetry]` | `OTEL_METRICS_EXPORTER` |

## The rule that matters most

**The work must run on the bus.** A span per DTO, a Sentry event per unexpected exception and the
metric labels all come from `bus(dto)`. A homemade dispatcher — a helper that takes the bus, a
function imported from another `services/` file, a lambda dependency — gets **nothing**. The symptom
is a production incident with nothing in Sentry and no span to open. See `sincpro_framework_use_cases`.

## Metrics — by itself, then by declaration

Every use case lands on `sincpro.use_case.duration` labelled with service, context, use case, layer
and outcome (`ok`, `expected`, or the failure's kind). A use case that wants more decorates itself —
**names come from the context, the use case and the field, never a string**:

```python
from sincpro_framework.observability.metrics import metrics, of

@billing.feature(CommandIssueInvoice)
@metrics.counts(by=of(CommandIssueInvoice).currency)
@metrics.sums(of(ResponseIssueInvoice).total, by=of(CommandIssueInvoice).currency, unit="BOB")
@metrics.measures(of(ResponseIssueInvoice).lines, buckets=(1, 5, 10, 50))
class IssueInvoice(Feature): ...
```

`of(Command).field` is a reference: the editor completes it, a rename renames it, a missing field
raises where written. Labels must be bounded (`Enum`, `Literal`, `bool`) — a customer or an invoice
number belongs on a span or in a log, never on a metric. For what a decorator cannot say, declare
an instrument on the class (`lookups = metrics.counter(by=...)`) and record inside `execute`.

## Errors

One exception type per layer, context in the message. **Expected** traffic (validation, "already
exists", auth, idempotency refusals) goes in `bus.ignore_sentry_exceptions(...)` — it is then an
`expected` metric outcome, not a failure. Unexpected exceptions must reach Sentry: that is why the
Feature runs on the bus.

**The handler trap:** what an error handler returns becomes the bus's answer. A handler written only
to watch returns `None` and silently swallows the failure — re-raise to delegate down the chain.

## Spans and correlation

- `with bus.with_trace(...)` to adopt an incoming `traceparent`; a span per DTO otherwise.
- `from sincpro_framework.observability import traces` then `@traces.attributes(...)` /
  `traces.annotate({...})` to put business fields on the span (a NIT, a merchant) — bounded keys, so
  Tempo filters without a series per value.
- **The same keys on all four signals** (service, version, tenant, context, use case, layer, outcome,
  error, trace_id), derived from configuration — no new wiring. `trace_id` is the jump between the
  metric exemplar, the log line, the GlitchTip tag and the span.

## References

- [references/traces-errors-logs.md](references/traces-errors-logs.md) — spans, error handlers, logs, embedding in a host (Odoo)
- [references/metrics.md](references/metrics.md) — instruments, labels, recorders, Prometheus/OTLP
- [references/correlation.md](references/correlation.md) — the keys on every signal, `APP_RELEASE`, filtering

## Related

- Errors are declared as expected at the bus: `sincpro-framework` (hard rules)
- The SQLAlchemy adapter reports every statement: `sincpro-framework-persistence`
