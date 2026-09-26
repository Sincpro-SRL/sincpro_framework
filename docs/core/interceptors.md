# Interceptors: around one use case, from outside it

An interceptor is a function that runs **around** the execution of a Command. It sees the
Command and the response; it can veto, adjust either, or answer by itself — and it never changes
what class either of them is.

```
framework(CommandCreateInvoice)          outer interceptor ─┐
self.feature_bus.execute(...)     ──▶      inner interceptor ─┤──▶  CreateInvoice.execute
a workflow step, a scheduled tick                             │
                                   ◀──  response adjusted ◀───┘
```

Every block on this page runs, in order, in `tests/docs/test_persistence_guide.py` — an example
that stops working fails the build.

## Which tool, for which rule

| The rule is about… | Use | Example |
|---|---|---|
| the shape of the input | the DTO (pydantic), automatically | `amount: int` |
| **this request**: who asks, with what, and what they get back | **an interceptor** | a credit check, an audit trail, a cached query |
| **the aggregate**, whoever writes it | a repository hook | "an invoice has to balance" |
| an error turned into an answer | an error handler | a SOAP fault → the SDK's exception |
| something **after**, in another context | an event | "when posted, email the customer" |
| **another handler answering instead** | `replaces=` (PRD_04) | the Bolivian tax rules |

## The contract

```python
from sincpro_framework import CallNext, DataTransferObject, Feature, UseFramework


class CommandCreateInvoice(DataTransferObject):
    customer_id: str
    total: int
    due_days: int = 0


class ResponseCreateInvoice(DataTransferObject):
    invoice_id: str
    due_days: int
    note: str = ""


def new_billing() -> UseFramework:
    billing = UseFramework("billing", log_after_execution=False)

    @billing.feature(CommandCreateInvoice)
    class CreateInvoice(Feature):
        def execute(self, dto: CommandCreateInvoice) -> ResponseCreateInvoice:
            return ResponseCreateInvoice(invoice_id=f"F-{dto.customer_id}", due_days=dto.due_days)

    return billing
```

```python
billing = new_billing()


@billing.interceptor(CommandCreateInvoice)
def thirty_days_by_default(
    dto: CommandCreateInvoice, call_next: CallNext[ResponseCreateInvoice]
) -> ResponseCreateInvoice:
    adjusted = dto if dto.due_days else dto.model_copy(update={"due_days": 30})
    return call_next(adjusted)


answer = billing(CommandCreateInvoice(customer_id="acme", total=100), ResponseCreateInvoice)
assert answer.due_days == 30
```

- `@bus.interceptor(CommandA, CommandB)` wraps those Commands; `@bus.interceptor()` wraps every
  Command of the bus.
- They run in the order they were registered, outermost first, **however the Command is
  executed**: `framework(dto)`, `self.feature_bus.execute(dto)` inside an ApplicationService, a
  workflow step, a scheduled tick.
- What reaches `call_next` must be the same Command class — change values with `model_copy`. What
  comes back must be the response class the handler answered. Anything else raises
  `InterceptorContractViolation`, naming the interceptor.
- An exception in an interceptor is reported like one in the handler, with `error_at` on the
  interceptor's line.
- Interceptors are fixed when the bus is built: registering one afterwards raises
  `BusAlreadyBuilt`, and naming a Command nobody answers raises when the bus is built.

## Recipes

Each one is a pattern to copy and adapt, not a piece of the framework.

### Veto: a credit check

```python
from sincpro_framework.ddd import ContractViolation

billing = new_billing()
blocked = {"moroso"}


@billing.interceptor(CommandCreateInvoice)
def credit_check(
    dto: CommandCreateInvoice, call_next: CallNext[ResponseCreateInvoice]
) -> ResponseCreateInvoice:
    if dto.customer_id in blocked:
        raise ContractViolation(f"{dto.customer_id} has no credit")
    return call_next(dto)


try:
    billing(CommandCreateInvoice(customer_id="moroso", total=100))
    raise AssertionError("the credit check should have refused it")
except ContractViolation:
    pass
```

### Audit: every Command, who asked and what came back

```python
from typing import Any

billing = new_billing()
audit_trail: list[dict[str, Any]] = []


@billing.interceptor()
def audit(dto: Any, call_next: CallNext[Any]) -> Any:
    response = call_next(dto)
    audit_trail.append({
        "command": type(dto).__name__,
        "user": billing.current_context().get("user.id"),
        "response": response,
    })
    return response


with billing.context({"user.id": "ana"}):
    billing(CommandCreateInvoice(customer_id="acme", total=100))

assert audit_trail[0]["command"] == "CommandCreateInvoice"
assert audit_trail[0]["user"] == "ana"
```

### Idempotency: the same request twice runs once

A client that retries, a webhook delivered twice. The key travels in the context, so the Command
needs no extra field.

```python
billing = new_billing()
answered: dict[str, Any] = {}
executions: list[str] = []


@billing.interceptor(CommandCreateInvoice)
def idempotent(
    dto: CommandCreateInvoice, call_next: CallNext[ResponseCreateInvoice]
) -> ResponseCreateInvoice:
    key = billing.current_context().get("idempotency_key")
    if key in answered:
        return answered[key]
    executions.append(dto.customer_id)
    response = call_next(dto)
    if key:
        answered[key] = response
    return response


for _ in range(2):
    with billing.context({"idempotency_key": "req-7"}):
        billing(CommandCreateInvoice(customer_id="acme", total=100))

assert executions == ["acme"]
```

### Cache: a query answered without the handler

```python
class QueryPrice(DataTransferObject):
    product: str


class ResponsePrice(DataTransferObject):
    price: int


catalog = UseFramework("catalog", log_after_execution=False)
lookups: list[str] = []


@catalog.feature(QueryPrice)
class GetPrice(Feature):
    def execute(self, dto: QueryPrice) -> ResponsePrice:
        lookups.append(dto.product)
        return ResponsePrice(price=30)


cache: dict[str, ResponsePrice] = {}


@catalog.interceptor(QueryPrice)
def cached(dto: QueryPrice, call_next: CallNext[ResponsePrice]) -> ResponsePrice:
    key = dto.model_dump_json()                  # the query's values are its identity
    if key not in cache:
        cache[key] = call_next(dto)
    return cache[key]


for _ in range(3):
    assert catalog(QueryPrice(product="coffee"), ResponsePrice).price == 30

assert lookups == ["coffee"]
```

### Retry: a lost race tries again on fresh data

```python
from sincpro_framework.ddd import StaleAggregate


class CommandPostEntry(DataTransferObject):
    entry_id: str


ledger = UseFramework("ledger", log_after_execution=False)
attempts: list[int] = []


@ledger.feature(CommandPostEntry)
class PostEntry(Feature):
    def execute(self, dto: CommandPostEntry) -> None:
        attempts.append(len(attempts) + 1)
        if len(attempts) < 3:
            raise StaleAggregate(f"entry {dto.entry_id} changed meanwhile")


@ledger.interceptor(CommandPostEntry)
def retry_when_stale(dto: CommandPostEntry, call_next: CallNext[None]) -> None:
    for attempt in range(3):
        try:
            return call_next(dto)
        except StaleAggregate:
            if attempt == 2:
                raise
    return None


ledger(CommandPostEntry(entry_id="e-1"))
assert attempts == [1, 2, 3]
```

### Feature flag: a behaviour on for some tenants only

```python
billing = new_billing()
early_payment_discount = {"acme"}


@billing.interceptor(CommandCreateInvoice)
def early_payment(
    dto: CommandCreateInvoice, call_next: CallNext[ResponseCreateInvoice]
) -> ResponseCreateInvoice:
    response = call_next(dto)
    if billing.current_context().get("tenant") in early_payment_discount:
        return response.model_copy(update={"note": "2% if paid within 10 days"})
    return response


with billing.context({"tenant": "acme"}):
    assert billing(CommandCreateInvoice(customer_id="c1", total=1), ResponseCreateInvoice).note

with billing.context({"tenant": "globex"}):
    assert not billing(CommandCreateInvoice(customer_id="c1", total=1), ResponseCreateInvoice).note
```

### Timing: how long each Command takes

```python
import time

billing = new_billing()
durations: dict[str, float] = {}


@billing.interceptor()
def timed(dto: Any, call_next: CallNext[Any]) -> Any:
    started = time.perf_counter()
    try:
        return call_next(dto)
    finally:
        durations[type(dto).__name__] = time.perf_counter() - started


billing(CommandCreateInvoice(customer_id="acme", total=100))
assert "CommandCreateInvoice" in durations
```

The framework already puts a span on every Command when OpenTelemetry is on; this is for a number
you want to act on inside the process.

## What runs, answered

```python
from sincpro_framework.introspection import features

billing = new_billing()
billing.interceptor()(timed)
billing.interceptor(CommandCreateInvoice)(credit_check)
billing.build_root_bus()

assert [name.rsplit(".", 1)[-1] for name in features(billing)["CommandCreateInvoice"].interceptors] == [
    "timed",
    "credit_check",
]
```

## Migration note: middleware (removed)

`add_middleware(fn)` and `sincpro_framework.Middleware` are gone. A middleware was a function
`dto -> dto` run once at `framework(dto)`; the same function as an interceptor wraps the handler
instead, runs however the Command is executed, and sees the response:

```python
def validate(dto: Any, call_next: CallNext[Any]) -> Any:     # was: def validate(dto): ...; return dto
    if getattr(dto, "amount", 1) <= 0:
        raise ValueError("amount must be positive")
    return call_next(dto)


migrated = new_billing()
migrated.interceptor()(validate)                              # was: migrated.add_middleware(validate)
assert migrated(CommandCreateInvoice(customer_id="acme", total=5), ResponseCreateInvoice)
```

A middleware that answered with a *different* DTO class has no equivalent: the bus routes by class,
and an interceptor keeps it.
