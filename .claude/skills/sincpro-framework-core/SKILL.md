---
name: sincpro-framework-core
description: Work the cross-cutting parts of a sincpro_framework bus — the context manager (correlation ids, user, propagation across buses, async/thread handoff), interceptors (veto, adjust, audit, cache, retry; replaces=) and error handlers (global/feature/app_service). Use whenever a task passes metadata through a call, wraps a use case from outside, replaces a use case, or maps exceptions to answers.
---

# sincpro-framework-core

The parts of the bus that are not a Feature: metadata that travels, code around a use case, and how a
failure becomes an answer. The Feature/ApplicationService recipe itself is in `sincpro-framework` and
`framework_code_style`.

## Context manager — metadata that travels with a call

Context is a `ContextVar`, isolated per execution and per bus. A Feature reads it as `self.context`.

```python
with app.context({"correlation_id": "123", "user.id": "admin"}) as app_with_context:
    result = app_with_context(dto)

# nested: inner overrides, inherits the rest
with app.context({"env": "prod", "user": "admin"}) as outer:
    with outer.context({"env": "staging"}) as inner:
        inner(dto)          # env="staging", user="admin"
```

- The context of a request **follows it into every bus it reaches**: a Feature calling
  `self.common(...)`, a subscriber through a `SyncQueue`, starts from the caller's context, adds its
  own on top, and nothing it adds flows back. Outside an execution nothing is inherited.
- `bus.current_context()` reads the context in play (read-only).
- **Threads do not inherit `contextvars`.** For a manual `ThreadPoolExecutor`, call
  `bus.thread_context()` **once per task** and submit `.execute`; for an async caller fanning out,
  `bus.get_async_bus()` (stateless — get it once, `await` many). `asyncio.to_thread` propagates
  context by itself.
- Key naming is hierarchical (`user.id`, `service.name`). Keep values small.

Depth: [references/context.md](references/context.md), `docs/core/context-manager.md`.

## Interceptors — change a use case from outside it

A function runs **around** a Command: it sees the Command and the response, can veto, adjust either,
or answer by itself — and never changes either's class. They run in registration order, outermost
first, **however the Command is executed**.

```python
from sincpro_framework import CallNext

@billing.interceptor(CommandCreateInvoice)
def credit_check(dto, call_next: CallNext[ResponseCreateInvoice]) -> ResponseCreateInvoice:
    if dto.customer_id in blocked:
        raise ContractViolation(f"{dto.customer_id} has no credit")
    return call_next(dto)
```

- `@bus.interceptor(CommandA, CommandB)` wraps those; `@bus.interceptor()` wraps every Command.
- Adjust the input with `dto.model_copy(update=...)`; what reaches `call_next` must be the same
  Command class, and what comes back the response class — else `InterceptorContractViolation`.
- Fixed when the bus is built (registering after raises `BusAlreadyBuilt`).
- Recipes: credit check, audit trail, idempotency, cache, retry on `StaleAggregate`, feature flag,
  timing. Each is a pattern to copy, not a framework piece.
- Ordering: `before=`, `after=`, `sequence=` (lower first, 10 default).

**`replaces=`** registers a handler that answers **instead** of the core's — the core does not run,
interceptors still wrap whichever answers:

```python
@tax.feature(CommandComputeTax, replaces=ComputeTax)
class ComputeTaxBolivia(Feature): ...
```

`replaces=` must name the handler registered now; the response contract stays (Liskov). It shows in
the build log, the span (`sincpro.replaces`) and `introspection.describe`.

Depth: [references/interceptors.md](references/interceptors.md), `docs/core/interceptors.md`, PRD_04.

## Error handlers — a failure becomes an answer

Three independent scopes: **global** (framework bus), **feature**, **app service**. First registered
runs first; re-raise to delegate to the next.

```python
framework.add_global_error_handler(handler)
framework.add_feature_error_handler(handler)
framework.add_app_service_error_handler(handler)
```

- Handlers can be added before or after the first execution.
- **What a handler returns becomes the bus's answer.** A handler written only to watch returns `None`
  and silently swallows the failure — re-raise (`raise error`) to delegate.
- `bus.ignore_sentry_exceptions(SomeError)` marks expected traffic: reported as an `expected` metric
  outcome, never to Sentry.
- An exception outside a Feature (a wrapper around the bus) skips the bus handlers; unexpected errors
  must be raised from a Feature.

Depth: [references/errors.md](references/errors.md), README "Error Handling".

## References

- [references/context.md](references/context.md) — context propagation, threads, async, error enrichment
- [references/interceptors.md](references/interceptors.md) — contract, recipes, ordering, `replaces=`
- [references/errors.md](references/errors.md) — scopes, chaining, the swallow trap

## Related

- Bootstrap, hard rules, DTO/dependency typing: `sincpro-framework`
- Metrics, traces and errors reporting: `sincpro-framework-observability`
- Auth is the outermost interceptor: `sincpro-framework-auth`
