---
name: sincpro-framework
description: Program any bounded context with sincpro_framework (UseFramework, Feature, ApplicationService, DataTransferObject). Use whenever you add a use case, a bounded context, a dependency, an aggregate, a repository, a Criteria query, a domain event, an entrypoint or observability to a Sincpro Python service (sincpro_mcp_odoo, sincpro_payments_sdk, sincpro_siat_soap, or a new one). This is the router; read it first, then the layer skill it points at.
---

# sincpro-framework

The application layer of every Sincpro Python service: one `UseFramework` per bounded context, a
`Feature`/`ApplicationService` per use case, both routed by the DTO they answer. The framework
gives, without configuring anything: a span per DTO, an error report on every unexpected exception,
and typed dependency injection.

This skill routes. Read the layer skill for what you are actually building, and the deep docs under
`docs/` when the short version is not enough.

> Version this skill was written against: **sincpro-framework 3.14.1**. If `pyproject.toml` pins a
> different minor, check the layer doc before trusting an example.

## The non-negotiables

These are the rules that, broken, produce an incident instead of a compile error.

1. **Every use case is a `Feature` or an `ApplicationService` on its context's bus.** Never a
   homemade registry, a class with `run()`, or a function that takes the bus as an argument.
2. **Reuse a use case by executing its Command on the injected bus.** Import only `Command*` and
   `Response*` from another service. Never import a helper, factory or Feature class. See
   `sincpro_framework_use_cases`.
3. **`domain/` is vocabulary and pure rules.** A function in `domain/` that takes an adapter as
   argument is I/O wearing a domain name. Shared I/O is an adapter registered in `dependencies.py`.
4. **Inject named components (classes) in `infrastructure/dependencies.py`.** Never a `lambda`.
5. **One bus per bounded context**, created *before* `services/` is imported — decorators register
   against that instance at import time.
6. **A service file exports only `Command*` and `Response*`.** The Feature class is for
   registration, not for import. Never `OldFeature = NewFeature`.
7. **`self` holds only injected dependencies.** Request data lives in locals — one handler instance
   serves every execution, on every thread.

Full reasoning and the review checklist: [references/hard-rules.md](references/hard-rules.md).

## The one decision: what artifact is this?

Ask in order. Stop at the first yes. Full detail in `implement_use_case`.

1. **Pure vocabulary / rule, no I/O?** → DTO or function in `domain/` (or `common/domain/`).
2. **I/O against an external system, reused by 2+ Features?** → named adapter in `adapters/`,
   registered in `dependencies.py`.
3. **One atomic operation?** → `Feature`. One file in `services/`: `Command*` + `Response*` +
   `@bus.feature(Command)` + `class VerbNoun(Feature)`.
4. **Two or more use cases that must succeed or fail together?** → `ApplicationService`, executing
   child Commands on `self.feature_bus`. Never another ApplicationService.
5. **A whole new vocabulary with its own lifecycle?** → new bounded context.

## The skeleton (every context)

```python
# infrastructure/framework.py
class Feature(_Feature, CtxDependencyContextType): ...
class ApplicationService(_ApplicationService, CtxDependencyContextType): ...

def config_ctx_framework(name: str) -> UseFramework[CtxDependencyContextType]:
    instance = UseFramework(name)
    register_dependencies(instance)
    return instance

# __init__.py — instance FIRST, then services (decorators need it)
ctx = config_ctx_framework("sincpro-ctx")
from . import services  # noqa: E402 — registers every use case
```

Folder layout, the three repo variants (`domains/`, `apps/`, one-context) and `common/` vs
`shared/` are in `framework_module_structure`. Dependency direction and what earns a place in
`common/` are in `framework_context_boundaries`.

## Capability map — where each layer lives

| I want to… | Layer skill | Prompt (MCP) | Deep doc |
|---|---|---|---|
| Add a Feature / ApplicationService / adapter / context | `implement_use_case` | `implement_use_case` | README, `docs/core/README.md` |
| Know where a file goes | `sincpro-framework` | `framework_module_structure` | `docs/architecture/ARCHITECTURE.md` |
| Decide a cross-context import | `sincpro-framework` | `framework_context_boundaries` | — |
| Context manager, interceptors, error handlers | **`sincpro-framework-core`** | `framework_core` | `docs/core/` |
| Model an aggregate, table, repository, unit of work | **`sincpro-framework-persistence`** | `framework_persistence` | `docs/persistence/guide.md` |
| Filter, order, page, count, aggregate, resolve relations | **`sincpro-framework-criteria`** | `framework_criteria` | `docs/persistence/criteria.md` |
| Record/publish domain events, outbox, event sourcing | **`sincpro-framework-domain-events`** | `framework_domain_events` | `docs/events/README.md` |
| Cache a Query's answer, idempotency, providers | **`sincpro-framework-caching`** | `framework_caching` | `docs/caching/README.md` |
| Traces, errors, logs, metrics, correlation | **`sincpro-framework-observability`** | `framework_observability` | `docs/observability/` |
| Configuration per context (cascade, env, secrets) | **`sincpro-framework-settings`** | `framework_settings` | `docs/core/settings.md` |
| Expose the bus (REST / RPC / gRPC / MCP / FastAPI / queue) | **`sincpro-framework-entrypoints`** | `framework_entrypoints` | `docs/entrypoints/` |
| Who is calling, what they may do | **`sincpro-framework-auth`** | `framework_auth` | `docs/auth/README.md` |
| Crons, migrations | **`sincpro-framework-operations`** | `framework_operations` | `docs/cron/`, `docs/migrations/` |
| DataFrames, runtime use cases, workflows | **`sincpro-framework-analytics`** | `framework_analytics` | `docs/data_analysis/`, `runtime_use_cases/`, `workflows/` |
| Pick the system shape (one DB / DB per context / facts as state) | — | `framework_capability_map` | `docs/shapes.md` |
| Runtime traps | — | `knowledge://framework_gotchas` | `AUDIT.md` |

For the framework's own design and reasoning, read `docs/architecture/ARCHITECTURE.md`, `docs/shapes.md`
and the PRDs under `docs/prd/`. The generated evidence index is `openwiki/index.md`.

## Verify before calling it done

```bash
make format          # black line length 94 + isort (isort does NOT reorder __init__.py)
make test            # pytest; a Feature is tested through its bus with faked adapters
make verify-format   # the gate
poetry run pyright sincpro_framework tests   # if the repo runs pyright
```

Test through the bus — `ctx(Command(...), Response)` — never `Feature().execute(...)`. What to
protect and what to delete is in `framework_critical_testing_strategy`.

## References

- [references/hard-rules.md](references/hard-rules.md) — the non-negotiables, with the incident each prevents
- [references/capability-map.md](references/capability-map.md) — one line per capability and the doc that owns it
- [references/bootstrap.md](references/bootstrap.md) — the skeleton, import order, and `DependencyContextType`
