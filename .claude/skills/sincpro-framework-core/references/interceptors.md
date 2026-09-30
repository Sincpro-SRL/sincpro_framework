# Interceptors and replacements

Depth: `docs/core/interceptors.md`, PRD_04. Every block there runs as a test.

## Which tool, for which rule

| The rule is about… | Use |
|---|---|
| the shape of the input | the DTO (pydantic), automatically |
| **this request**: who asks, with what, what they get back | an **interceptor** |
| **the aggregate**, whoever writes it | a repository hook |
| an error turned into an answer | an error handler |
| something **after**, in another context | an event |
| **another handler answering instead** | `replaces=` |

## The contract

```python
@billing.interceptor(CommandCreateInvoice)
def thirty_days_by_default(dto, call_next: CallNext[ResponseCreateInvoice]) -> ResponseCreateInvoice:
    adjusted = dto if dto.due_days else dto.model_copy(update={"due_days": 30})
    return call_next(adjusted)
```

- `@bus.interceptor(CommandA, CommandB)` wraps those; `@bus.interceptor()` wraps every Command.
- Run in the order registered, outermost first, **however the Command is executed** — `bus(dto)`,
  `self.feature_bus.execute(dto)`, a workflow step, a scheduled tick.
- What reaches `call_next` must be the same Command class (change values with `model_copy`); what
  comes back the response class the handler answered — else `InterceptorContractViolation`.
- An exception is reported with `error_at` on the interceptor's line. Interceptors are fixed when the
  bus is built (`BusAlreadyBuilt` afterwards; a Command nobody answers raises at build).

## Recipes

- **Veto** — a credit check raises `ContractViolation`.
- **Audit** — `@bus.interceptor()` records command, `bus.current_context().get("user.id")` and the
  response.
- **Idempotency** — the key travels in the context; cache the answer, run once (the framework's real
  version is `sincpro-framework-caching`).
- **Cache a Query** — keyed by `dto.model_dump_json()` (the framework's real version is
  `QueryCaching`).
- **Retry on `StaleAggregate`** — attempt up to N, re-raise the last.
- **Feature flag** — adjust the response for some tenants.
- **Timing** — a number you act on inside the process (the framework already spans every Command).

## Ordering, replacing, switching off

Extension points share one ordering (`sincpro_framework.ordering`): `before=` / `after=` by reference,
then `sequence=` (lower first, 10 when not said), then registration order.

```python
extended.interceptor(CommandCreateInvoice, sequence=50)(audit)
extended.interceptor(CommandCreateInvoice)(limits)
extended.interceptor(CommandCreateInvoice, replaces=limits)(limits_by_segment)
extended.interceptor(CommandCreateInvoice, before=[limits_by_segment])(trace)
extended.without_interceptor(audit)
# runs: trace, limits by segment
```

Error handlers take the same `replaces=`, `before=`, `after=`, `sequence=`.

## Replacing a use case

```python
@tax.feature(CommandComputeTax)
class ComputeTax(Feature): ...                       # the core

@tax.feature(CommandComputeTax, replaces=ComputeTax)  # an addon answers instead
class ComputeTaxBolivia(Feature): ...
```

- The core handler **does not run**. Interceptors still wrap whichever answers.
- `replaces=` must name the handler registered now; a replacement names the one it replaces.
- The contract stays (Liskov): when both declare their response, the replacement answers that class
  or a narrower one.
- It shows: the build log, the span (`sincpro.replaces`), `introspection.describe`.

## Migration note

`add_middleware(fn)` and `Middleware` are gone. A middleware `dto -> dto` is an interceptor
`(dto, call_next) -> response` — it wraps the handler, runs however the Command is executed, and sees
the response.
