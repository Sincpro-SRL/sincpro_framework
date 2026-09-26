# PRD_04: Extension points — change a use case without touching it

- **Status**: proposal
- **Depends on**: nothing
- **Unblocks**: PRD_06 (runtime definitions), addons
- **Removes**: the middleware pipeline (`add_middleware`, `Middleware`)

## Problem

A bounded context ships use cases. Another package — an addon, a customer's customisation,
another context — sometimes has to change how one of them behaves, and today it has four
options, none of them good:

| Today | What goes wrong |
|---|---|
| `add_middleware(fn)` | Runs once, at `framework(dto)`, **before** only: it cannot see the response, cannot target one Command, and does not run when an ApplicationService executes a Feature through `self.feature_bus` |
| Register a second handler for the same DTO | Refused (`DTOAlreadyRegistered`, `ioc.py`) — by design |
| Subclass the core Feature | The subclass is a different class the bus never routes to |
| Monkeypatch | Invisible, import-order dependent, breaks on every release |

**The middleware has a second, deeper problem.** It was designed when DTOs were routed by name:
a middleware could answer with a *different* DTO type. The bus now routes by **class**, so the
pipeline rewrites `processed_dto.__class__ = original_dto_class` (`middleware.py`) to make the
answer route again — a monkeypatch at the heart of every call, and an object whose class says one
thing and whose fields may say another (the audit already had to add a guard for the case where it
lost fields). A new extension point must not inherit it.

**Nobody outside the framework uses it.** `sincpro_mcp_odoo` and `sincpro_synthesis` call
`add_middleware`, but on Starlette and FastMCP, not on the bus. Only the framework's own tests use
the bus middleware — so it can be replaced rather than stretched.

## Goals

1. Wrap one use case — validate, veto, adjust its input or its response — from outside it.
2. Replace one use case, **explicitly**: the core handler is skipped, and everything that
   observes the bus says so and says by whom.
3. Answer, for any Command, what actually runs: which handler, what it replaced, what wraps it.
4. **One** extension concept, typed end to end: a Command stays its own class all the way through.

## Non-goals

- Changing the core's classes or modules at runtime (monkeypatching, subclass swapping).
- Interceptors defined as data at runtime — that is PRD_06.
- Security or permissions for extensions: the consumer decides what it installs.

## Prior art

| | Mechanism | What we take |
|---|---|---|
| MediatR (.NET CQRS) | `IPipelineBehavior<TRequest, TResponse>`: `Handle(request, next)` around one request type | The shape: per request type, around, `next` |
| NestJS interceptors | `intercept(context, next)`, `next.handle()` returns the response to transform | The name, and transforming the response |
| gRPC server interceptors, Axon `MessageHandlerInterceptor` | Around one call / one message | "Interceptor" as the industry word for exactly this |
| Dynamics 365 | Pre/post operation stages on one message | Veto before, adjust after, same transaction |
| Frappe | `override_doctype_class`: "only the last app wins", silently | What **not** to do: an override that hides another |

## The model: one table to choose

| I need to… | Use | Exists |
|---|---|---|
| do something **as well**, after the fact | an event and a subscriber | yes |
| decide **differently** at a point the core foresaw (tax, pricing) | a port: the core calls `self.tax_policy`, the addon registers another one | yes |
| validate, veto or adjust **around** one use case | `@bus.interceptor(Command)` | **new** |
| have **another handler answer instead** of the core | `@bus.feature(Command, replaces=CoreFeature)` | **new** |

### 1. Interceptors

```python
from sincpro_framework import CallNext

@billing.interceptor(CommandCreateInvoice)
def credit_check(
    dto: CommandCreateInvoice, call_next: CallNext[ResponseCreateInvoice]
) -> ResponseCreateInvoice:
    if not credit.allows(dto.customer_id):
        raise ContractViolation(f"{dto.customer_id} has no credit")   # veto: the core never runs
    adjusted = dto.model_copy(update={"due_days": 30})               # same class, other values
    response = call_next(adjusted)
    return response.model_copy(update={"note": "credit checked"})     # adjust the answer
```

- **Typed, never re-typed.** What an interceptor passes to `call_next` must be the same Command
  class it received, and what it returns must be the handler's response class. Anything else is
  refused at the call, naming the interceptor. There is no class rewriting anywhere.
- **Where it runs**: around the handler, on the `FeatureBus` / `ApplicationServiceBus` — so it runs
  however the Command is executed: `framework(dto)`, `self.feature_bus.execute(dto)` inside an
  ApplicationService, a workflow step, an event, a scheduled tick.
- **Inside the handler's span, its error handlers and its failure report**: an exception raised in
  an interceptor is reported like one from the handler, with `error_at` on the interceptor's line.
- **Several Commands**: `@billing.interceptor(CommandA, CommandB)`. **Every Command of the bus**:
  `@billing.interceptor()` — for cross-cutting concerns (audit, timing).
- **Order**: the order they were registered, outermost first. An addon loader registers in
  dependency order, so the order is deterministic.
- An interceptor that does not call `call_next` answers by itself — that is what a cache does. To
  replace the use case for good, use `replaces=` so it shows.

### 2. Replacing a use case, explicitly

```python
@billing.feature(CommandComputeTax, replaces=ComputeTax)
class ComputeTaxBolivia(Feature):
    def execute(self, dto: CommandComputeTax) -> ResponseComputeTax: ...
```

- `replaces=` **names the class it replaces**. The registration is refused unless that class is the
  one currently registered for every Command listed — so a replacement cannot land on the wrong
  handler, and cannot land before the core registered its own.
- **Two replacements of the same handler are refused**, naming both:
  `ComputeTaxPeru replaces ComputeTax, but ComputeTaxBolivia already did — replace ComputeTaxBolivia
  or remove it`. Chaining is explicit: `replaces=ComputeTaxBolivia`.
- **The contract is the core's** (Liskov): when both declare the return type of `execute`, the
  replacement's must be the same class or a subclass; anything else is refused at registration.
- **The core handler does not run.** Interceptors still wrap whichever handler answers.
- Same parameter on `@bus.app_service(...)`.

It is visible everywhere:

| Where | What it says |
|---|---|
| building the bus | `info: CommandComputeTax is handled by ComputeTaxBolivia (replaces ComputeTax)` |
| every execution | span attribute `sincpro.replaces = "ComputeTax"`; failure fields carry the handler that ran |
| introspection | `billing.describe(CommandComputeTax)` |
| entrypoints | the catalog serves the same description over MCP / JSON-RPC / gRPC |

### 3. What runs, answered

Phase 1 already answers the interceptors: `features(bus)["CommandCreateInvoice"].interceptors` in
`sincpro_framework.introspection`, outermost first. Phase 2 adds the handler and what it replaced:

```python
billing.describe(CommandComputeTax)
# Handling(
#     command="CommandComputeTax",
#     layer="feature",
#     handler="bolivia_tax.features.ComputeTaxBolivia",
#     replaces=("billing.services.compute_tax.ComputeTax",),
#     interceptors=("credit.credit_check", "audit.audit_every_command"),
# )
```

`describe()` with no argument answers every Command of the bus. It lives in the introspection
component, so the catalog and a test read the same thing.

## What happens to `add_middleware`

**Removed**, together with `MiddlewarePipeline` and `sincpro_framework.Middleware`: nobody outside
the framework used it, and keeping it would keep the `__class__` rewrite. A `dto -> dto` function
becomes an interceptor that ends in `call_next(dto)`; `docs/core/interceptors.md` has the migration
note, run by the docs test.

## SOLID, point by point

| Principle | Where it shows |
|---|---|
| Open/closed | The core module is never edited; interceptors, `replaces=`, ports and events are the only ways in |
| Liskov | A replacement honours the replaced handler's Command and response; an interceptor keeps both types |
| Dependency inversion | Ports: the core depends on `TaxPolicy`, the addon provides one |
| Single responsibility | One interceptor, one concern; one replacement, one use case |
| Interface segregation | An interceptor names the Commands it cares about and sees only those DTOs |

## Design notes

- The chain is built once per Command at `build_root_bus()`, not per call.
- Registering an interceptor or a replacement **after** the bus was built is an error — today a
  late registration is ignored silently (PRD_06/07 are how definitions change at runtime).
- No new container provider: a replacement swaps the `Factory` behind the DTO in
  `feature_registry` / `app_service_registry`; the interceptor chain wraps the resolved handler.

## Tests that pin it

1. An interceptor sees the DTO and the response; one that raises vetoes; one that does not call
   `call_next` answers.
2. An interceptor that passes another Command class, or returns another response class, is refused.
3. An interceptor runs when the Command is executed from an ApplicationService.
4. Two interceptors run in registration order.
5. `replaces=` naming the wrong class, before the core registered, or twice → refused, with the
   message naming both.
6. A replacement with an incompatible return type → refused.
7. The replaced core handler never runs; the span and `describe()` say who replaced it.
8. A failure inside an interceptor is reported with `error_at` on the interceptor.

## Phases

1. `@bus.interceptor(*commands)` + `CallNext` + type checks + ordering + observability; the
   middleware pipeline removed.
2. `replaces=` on `feature` / `app_service` + refusals + `describe()`.
3. Catalog exposes `describe()`; entrypoints serve it.
