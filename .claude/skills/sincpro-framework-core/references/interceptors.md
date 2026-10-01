# Interceptors and replacements

Source of truth: `sincpro_framework/interceptors.py`, `UseFramework.interceptor` in `use_bus.py`
and `sincpro_framework/ordering.py`. The framework repository has the long-form guide
(`docs/core/interceptors.md`, every block runs as a test) and the design (PRD_04).

## Which tool, for which rule

| The rule is about… | Use |
|---|---|
| the shape of the input | the DTO (pydantic), automatically |
| **this request**: who asks, with what, what they get back | an **interceptor** |
| **the aggregate**, whoever writes it | a repository hook |
| an error turned into an answer | an error handler |
| something **after**, in another context | an event |
| **another handler answering instead** | `replaces=` |

There is no separate middleware API: an interceptor is the one way to run code around a Command.

## The contract

```python
@billing.interceptor(CommandCreateInvoice)
def thirty_days_by_default(dto, call_next: CallNext[ResponseCreateInvoice]) -> ResponseCreateInvoice:
    adjusted = dto if dto.due_days else dto.model_copy(update={"due_days": 30})
    return call_next(adjusted)
```

- `@bus.interceptor(CommandA, CommandB)` wraps those; `@bus.interceptor()` wraps every Command of
  the bus, Features and ApplicationServices alike, nested executions included.
- Run outermost first, **however the Command is executed**: `bus(dto)`,
  `self.feature_bus.execute(dto)`, a workflow step, a scheduled tick.
- What reaches `call_next` must be the same Command class (change values with `model_copy`); what
  comes back must be the class the handler answered, else `InterceptorContractViolation`, naming
  the interceptor. An interceptor that never calls `call_next` answers alone and is not checked.
- An exception in an interceptor goes to the same error chain as the handler's and is reported
  with `error_at` on the interceptor's line.
- Interceptors are fixed when the bus is built: `BusAlreadyBuilt` afterwards; a Command nobody
  answers raises `UnknownDTOToExecute` at build. The decorator only runs when its module is
  imported: import the interceptors module from the context's `__init__.py`.

## Recipes

Each is a pattern to copy, not a framework piece.

- **Veto**: a credit check raises `ContractViolation` (`sincpro_framework.ddd`).
- **Audit**: `@bus.interceptor(CommandA, CommandB)` records the command,
  `bus.current_context().get("user.id")` and the response. Name the Commands: with none it also
  runs on every Feature an ApplicationService calls.
- **Idempotency**: the key travels in the context; cache the answer, run once (the framework's own
  version is in `sincpro-framework-caching`).
- **Cache a Query**: keyed by `dto.model_dump_json()` (the framework's own version is
  `QueryCaching`, `sincpro-framework-caching`).
- **Retry on `StaleAggregate`** (`sincpro_framework.ddd`): attempt up to N, re-raise the last.
- **Feature flag**: adjust the response for some tenants.
- **Timing**: a number you act on inside the process (the framework already spans every Command).

```python
@billing.interceptor(CommandPostInvoice)
def retry_stale(dto, call_next: CallNext[ResponsePostInvoice]) -> ResponsePostInvoice:
    for attempt in range(3):
        try:
            return call_next(dto)
        except StaleAggregate:
            if attempt == 2:
                raise
    raise AssertionError("unreachable")
```

## Ordering, replacing, switching off

Extension points share one ordering (`sincpro_framework.ordering`): `before=` / `after=` by
reference, then `sequence=` (lower first, 10 when not said), then registration order. A cycle of
`before`/`after` raises `ExtensionRefused`; a constraint on something not registered is ignored.

```python
extended.interceptor(CommandCreateInvoice, sequence=50)(audit)
extended.interceptor(CommandCreateInvoice)(limits)
extended.interceptor(CommandCreateInvoice, replaces=limits)(limits_by_segment)
extended.interceptor(CommandCreateInvoice, before=[limits_by_segment])(trace)
extended.without_interceptor(audit)
# runs: trace, limits by segment
```

- A replacement takes the place (position, sequence, constraints) of the interceptor it names.
  One that wraps other Commands than the one it replaces is a build-time warning.
- Error handlers take the same `replaces=`, `before=`, `after=`, `sequence=`.

## Replacing a use case

```python
@tax.feature(CommandComputeTax)
class ComputeTax(Feature): ...                       # the core


@tax.feature(CommandComputeTax, replaces=ComputeTax)  # an addon answers instead
class ComputeTaxBolivia(Feature): ...
```

- The core handler **does not run**. Interceptors still wrap whichever answers.
- `replaces=` must name the handler registered at that point: naming another class, or replacing before the
  core registered, is refused. A replacement of a replacement names the latest
  (`replaces=ComputeTaxBolivia`). The same parameter exists on `@bus.app_service(...)`.
- The contract stays (Liskov): when both declare their response, the replacement answers that class
  or a narrower one.
- It shows: the build log (`CommandComputeTax is handled by ComputeTaxBolivia (replaces
  ComputeTax)`), the span (`sincpro.replaces`), and introspection.

## What runs, answered

```python
from sincpro_framework.introspection import describe, features

handling = describe(tax, CommandComputeTax)
handling.type           # ComputeTaxBolivia
handling.replaces       # ("module.ComputeTax",)
features(billing)["CommandCreateInvoice"].interceptors   # names, outermost first
```
