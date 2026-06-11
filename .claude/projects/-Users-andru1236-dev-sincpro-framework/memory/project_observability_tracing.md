---
name: project-observability-tracing
description: Observability/tracing feature (PRD_03) — implemented and merged. with_trace() API, OTel spans on buses, log correlation.
metadata:
  type: project
---

PRD_03 observability/tracing implemented in full (June 2026).

**Why:** Production visibility — correlate logs to executions, parent-child spans for AppService→Feature chains, connect to outer traces (FastAPI, Celery).

**What was built:**
- `sincpro_framework/tracing/__init__.py` — `setup_otlp_provider(service_name)`, lazy SDK import
- `sincpro_framework/tracing/span_context.py` — `FrameworkSpanContext` context manager
- `bus.py` — `_span()` helper + OTel span wrapping on `FeatureBus.execute` and `ApplicationServiceBus.execute`
- `use_bus.py` — `with_trace(trace_id, span_id, carrier)` method + `setup_otlp_provider` in `build_root_bus()`
- `tests/tracing/test_tracing.py` — 18 tests (12 non-OTel, 6 OTel)
- `pyproject.toml` — OTel as optional extra + dev deps

**Key architectural decisions:**
1. OTel is completely optional — all imports are lazy (`try/except ImportError` + deferred in methods). Zero behavior change without OTel.
2. Log correlation uses `sincpro_log`'s existing `logger.context()` API — no structlog reconfiguration. Works because all buses share the same `LoggerProxy` instance.
3. `with_trace()` without args creates a root container span named after `_logger_name` (bundled_context_name), so log `trace_id` matches OTel trace_id.
4. `trace_id`/`span_id` injected into framework context → Features read via `self.context.get("trace_id")`.
5. OTel tests use session-scoped fixture (OTel only allows setting provider once globally) + function-scoped clear.

**Known limitation (future work):** `sincpro_log`'s `LoggerProxy._temporal_fields` is instance state, not `contextvars` — concurrent async workloads can bleed trace fields between coroutines. Tracked in PRD_03 as future `sincpro_log` improvement.

**How to apply:** When adding features that need observability, use `with_trace()` at the outer boundary. Bus-level spans are automatic.
