# PRD_03: Observability & Tracing — Sincpro Framework

## Overview

- **Priority**: High
- **Extra**: `sincpro-framework[opentelemetry]` (OTel is fully optional)
- **Scope**: Log correlation (always) · OTel spans (optional) · OTLP export (optional)

---

## Problem

The framework executes Features and ApplicationServices with no visibility into
the execution chain. In production it is impossible to:

- Correlate log lines to a specific execution or distributed request.
- See the parent → child span hierarchy when an ApplicationService calls Features.
- Connect to an existing trace started by an outer layer (FastAPI, Celery, etc.).

---

## Design principles

1. **Zero-dependency opt-out** — `opentelemetry-api` is NOT a base dependency.
   Everything in the framework works identically whether OTel is installed or not.
   All OTel imports are lazy (`try/except ImportError`).

2. **Log correlation is always available** — `with_trace()` works without OTel.
   When OTel is absent it binds `trace_id`/`span_id` using `sincpro_log`'s existing
   `logger.tracing()` API. No structlog reconfiguration, no global side effects.

3. **OTel spans are additive** — when `opentelemetry-sdk` is installed and a
   provider is configured, spans are created automatically on top of log
   correlation. Nothing breaks if the SDK is removed.

4. **Zero flag to toggle OTel** — no `enabled`/`disabled` setting. If a real
   `TracerProvider` is configured, spans are exported. If not, they are no-op.

5. **Same context manager pattern** — `with_trace()` follows the same idiom as
   `with framework.context({...})`. Both are context managers on `UseFramework`,
   both are composable.

6. **Auto-config at `build_root_bus()`**, not at `__init__`** — OTLP provider
   setup runs when the bus is built (explicit), not when `UseFramework` is
   instantiated (implicit).

---

## Key insight: shared logger instance

All buses share **the same `LoggerProxy` instance** created in `UseFramework`:

```python
# use_bus.py
self._sp_container = ioc.FrameworkContainer(logger_bus=self.logger)
# FeatureBus, ApplicationServiceBus, FrameworkBus all receive self.logger
```

This means binding `trace_id`/`span_id` on `self.logger` via `logger.tracing()`
propagates to all internal framework logs automatically — no structlog
reconfiguration needed.

---

## Naming

| Field              | Value                                                                  |
|--------------------|------------------------------------------------------------------------|
| `service.name`     | `bundled_context_name` — `UseFramework("cybersource")` → `"cybersource"` |
| span name          | DTO class name — `TokenizationParams`, `PaymentServiceParams`          |
| `sincpro.layer`    | `"feature"` or `"application_service"`                                 |
| `trace_id` in logs | OTel hex string when OTel active; UUID when OTel absent                |
| `span_id` in logs  | OTel hex string when OTel active; UUID when OTel absent                |

---

## Span hierarchy (when OTel is installed)

OTel uses `contextvars` internally. `start_as_current_span()` detects any active
span automatically:

```
[outer span — FastAPI, Celery, gRPC interceptor …]   ← adopted if present
  └── PaymentServiceParams                           ← ApplicationServiceBus span
        └── TokenizationParams                      ← FeatureBus span (auto child)
        └── ChargeCardParams                        ← FeatureBus span (auto child)
```

If there is no outer span, `PaymentServiceParams` becomes the root trace.

When OTel is **not** installed, the hierarchy is reflected only in log correlation:
each execution block has the same `trace_id`; each bus level gets a `span_id`.

---

## Public API

### Without OTel installed — log correlation only

```python
cybersource = UseFramework("cybersource")

with cybersource.with_trace() as traced:
    result = traced(TokenizationParams(...))
# → all logs in that block contain trace_id and span_id (auto-generated UUIDs)
# → no OTel spans, no export — framework still works identically
```

### With OTel installed + OTLP endpoint set — full tracing

```python
# pip install sincpro-framework[opentelemetry]
# OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317

cybersource = UseFramework("cybersource")

with cybersource.with_trace() as traced:
    result = traced(TokenizationParams(...))
# → span "TokenizationParams", service.name="cybersource", exported via OTLP
# → all logs contain OTel trace_id and span_id
```

### Explicit propagation from outer layer

```python
# Propagate from raw trace_id / span_id (e.g. read from internal headers)
with cybersource.with_trace(trace_id="4bf92f3577b3...", span_id="00f067aa0ba9...") as traced:
    result = traced(TokenizationParams(...))

# Extract from W3C traceparent header (requires OTel installed)
with cybersource.with_trace(carrier=request.headers) as traced:
    result = traced(TokenizationParams(...))
```

### Composition with `context()`

`with_trace()` is orthogonal to `context()` — both can be composed freely:

```python
with cybersource.with_trace(carrier=headers) as traced:
    with traced.context({"user.id": "u-123"}) as app:
        result = app(TokenizationParams(...))
```

### Access trace context from Feature / ApplicationService

Because `with_trace()` also writes `trace_id` and `span_id` into the framework
context, user code can read them without importing anything from `tracing/`:

```python
@cybersource.feature(TokenizationParams)
class TokenizeFeature(Feature):
    def execute(self, dto: TokenizationParams):
        trace_id = self.context.get("trace_id")   # available automatically
        span_id = self.context.get("span_id")
        ...
```

---

## Log correlation detail

`sincpro_log`'s `LoggerProxy` already has `logger.tracing(trace_id, request_id)`
which stores fields in `_temporal_fields` and merges them into every structlog
call within the block.

`with_trace()` implementation:

```python
with self.logger.tracing(trace_id=trace_id, request_id=span_id):
    # all bus logs (FeatureBus, ApplicationServiceBus, FrameworkBus)
    # automatically include trace_id + span_id because they share self.logger
    yield self
```

No changes to `sincpro_log` are required for the initial version.

> **Note (future)**: `LoggerProxy._temporal_fields` is instance-level state, not
> `contextvars`. Under concurrent async workloads this can cause field bleed
> between coroutines. A future `sincpro_log` improvement should migrate
> `_temporal_fields` to `contextvars.ContextVar` (backwards-compatible API).

---

## Auto-configuration logic (OTel path)

```
build_root_bus() called
  └── opentelemetry-sdk installed?
        NO  → skip, no-op
        YES → check OTEL_EXPORTER_OTLP_ENDPOINT
                not set → skip (outer app may configure its own provider)
                set     → check trace.get_tracer_provider()
                            ProxyTracerProvider or NoOpTracerProvider?
                              YES → configure TracerProvider + BatchSpanProcessor
                                    + OTLPSpanExporter for this service_name
                              NO  → respect existing provider, do nothing
```

---

## Module structure

```
sincpro_framework/
  tracing/
    __init__.py          # public: setup_otlp_provider
    span_context.py      # FrameworkSpanContext — with_trace() context manager
```

`log_correlation.py` from the original PRD is dropped — log correlation is
handled directly by `sincpro_log`'s `logger.tracing()` within `FrameworkSpanContext`.

---

## Implementation plan

### Phase 1 — `pyproject.toml`

`opentelemetry-api` is **not** added to base dependencies.

```toml
[tool.poetry.extras]
opentelemetry = [
    "opentelemetry-api",
    "opentelemetry-sdk",
    "opentelemetry-exporter-otlp-proto-grpc",
    "opentelemetry-exporter-otlp-proto-http",
]
```

All OTel imports in the framework use lazy guard:

```python
try:
    from opentelemetry import trace as otel_trace
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
    _OTEL_AVAILABLE = True
except ImportError:
    _OTEL_AVAILABLE = False
```

### Phase 2 — `tracing/span_context.py`

`FrameworkSpanContext` — context manager returned by `with_trace()`.

**`__enter__`**:
1. Resolve `trace_id` / `span_id`:
   - If `carrier` provided and OTel available → extract via W3C propagator
   - If `trace_id`/`span_id` provided explicitly → use them (construct `NonRecordingSpan` if OTel available)
   - Otherwise → auto-generate (OTel root span if available, UUID fallback)
2. If OTel available → attach span to OTel contextvars token
3. Call `self.logger.tracing(trace_id=trace_id, request_id=span_id)` → all bus logs get fields
4. Inject `trace_id`/`span_id` into framework context via `_set_context()` → Features read from `self.context`
5. Return `UseFramework` instance (same as `FrameworkContext.__enter__`)

**`__exit__`**:
1. If OTel → detach contextvars token
2. `logger.tracing()` context manager exit restores previous `_temporal_fields`
3. Restore previous framework context

### Phase 3 — `tracing/__init__.py`

`setup_otlp_provider(service_name: str)`:
- Runs only if OTel SDK available and `OTEL_EXPORTER_OTLP_ENDPOINT` is set
- Checks `trace.get_tracer_provider()` before registering to avoid double-config
- Creates `TracerProvider` with `Resource({"service.name": service_name})`
- Adds `BatchSpanProcessor(OTLPSpanExporter())`

### Phase 4 — Instrument `bus.py`

Applied to `FeatureBus.execute` and `ApplicationServiceBus.execute`:

```python
# Lazy at module level
try:
    from opentelemetry import trace as otel_trace
    from opentelemetry.trace import StatusCode
    _OTEL_AVAILABLE = True
except ImportError:
    _OTEL_AVAILABLE = False

# Inside execute():
if _OTEL_AVAILABLE:
    tracer = otel_trace.get_tracer("sincpro_framework")
    with tracer.start_as_current_span(
        dto_name,
        attributes={
            "sincpro.layer": "feature",   # or "application_service"
        },
    ) as span:
        try:
            return registry[dto_name].execute(dto)
        except Exception as error:
            span.record_exception(error)
            span.set_status(StatusCode.ERROR, str(error))
            raise
else:
    return registry[dto_name].execute(dto)
```

`start_as_current_span` detects and inherits any active parent span automatically.

### Phase 5 — `use_bus.py` additions

- `build_root_bus()`: call `setup_otlp_provider(self._logger_name)` after bus build
- `with_trace(trace_id=None, span_id=None, carrier=None) → FrameworkSpanContext`

### Phase 6 — Tests

`tests/tracing/test_tracing.py`:

| Test | What it validates |
|------|-------------------|
| `test_no_otel_no_error` | Framework runs normally without `[opentelemetry]` |
| `test_with_trace_generates_ids` | `with_trace()` with no args auto-generates `trace_id`/`span_id` |
| `test_with_trace_explicit_ids` | `with_trace(trace_id, span_id)` uses provided values |
| `test_trace_ids_in_framework_context` | `trace_id`/`span_id` available in Feature via `self.context` |
| `test_logs_have_trace_id` | Logger contains `trace_id` and `span_id` inside `with_trace` block |
| `test_log_fields_cleared_after_exit` | No trace fields leak outside `with_trace` block |
| `test_context_api_composable` | `with_trace()` + `context()` compose without conflict |
| `test_span_name_equals_dto_name` | (OTel) Span name matches `dto.__class__.__name__` |
| `test_span_attributes` | (OTel) `sincpro.layer` present on span |
| `test_app_service_child_spans` | (OTel) Feature spans are children of AppService span |
| `test_adopts_outer_active_span` | (OTel) Active span in contextvars becomes parent |
| `test_with_trace_carrier` | (OTel) W3C `traceparent` in carrier used as parent |
| `test_error_recorded_in_span` | (OTel) Exception sets span status `ERROR` |

Tests marked `(OTel)` use `opentelemetry-sdk` in-memory exporter and are
skipped automatically if the extra is not installed.

---

## Out of scope

- Metrics (Prometheus, StatsD, histograms)
- Baggage propagation
- Jaeger-native protocol (OTLP covers Jaeger, Tempo, and any OTLP backend)
- Changing `Feature.execute` or `ApplicationService.execute` signatures
- Multi-provider or custom exporter factory beyond OTLP
- `sincpro_log` async safety (`ContextVar` migration) — tracked separately
