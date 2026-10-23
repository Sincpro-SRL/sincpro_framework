# Error handlers: turning a failure into an answer

An error handler is a function that receives an exception and either **answers** — what it
returns becomes what the bus returns, and the error is gone — or **re-raises**, passing the error
on. A bus has three chains of them, one per layer, and a failure crosses them from the inside out.

Every block on this page runs, in order, in `tests/docs/test_persistence_guide.py`.

## Where they sit

```text
bus(dto) ─► FrameworkBus ─► ApplicationServiceBus ─► ApplicationService.execute
                │                    │                       │ self.feature_bus.execute(...)
                │                    │                       ▼
                │                    │               FeatureBus ─► Feature.execute ── raises
                │                    │                   │
                │                    │          feature chain: answers? ─► the ApplicationService
                │                    │                   │ re-raised      gets that answer
                │                    │                   ▼
                │                    │      the ApplicationService's code may catch it
                │        application service chain: answers? ─► the caller gets that answer
                │                    │ re-raised
                ▼                    ▼
          global chain: answers? ─► the caller gets that answer
                │ re-raised
                ▼
          the caller of bus(dto) gets the exception
```

| Chain | Registered with | Sees |
|---|---|---|
| Feature | `bus.add_feature_error_handler(handler)` | What a Feature raised, or one of its interceptors |
| Application service | `bus.add_app_service_error_handler(handler)` | What an ApplicationService raised — including a Feature's error it did not catch — or one of its interceptors |
| Global | `bus.add_global_error_handler(handler)` | Everything that reaches the root: what the layers did not answer, and the bus's own errors (a DTO nobody answers) |

## Answering, or passing it on

```python
from sincpro_framework import DataTransferObject, Feature, UseFramework


class CommandPay(DataTransferObject):
    amount: int


class ResponsePay(DataTransferObject):
    paid: int
    error: str = ""


class CardDeclined(Exception):
    pass


def new_payments() -> UseFramework:
    payments = UseFramework("payments-errors", log_after_execution=False)

    @payments.feature(CommandPay)
    class Pay(Feature):
        def execute(self, dto: CommandPay) -> ResponsePay:
            if dto.amount > 100:
                raise CardDeclined("over the card's limit")
            return ResponsePay(paid=dto.amount)

    return payments


payments = new_payments()
payments.add_feature_error_handler(lambda error: ResponsePay(paid=0, error=str(error)))

assert payments(CommandPay(amount=500), ResponsePay) == ResponsePay(
    paid=0, error="over the card's limit"
)
```

**The trap.** A handler written only to *look* at the error returns `None` without meaning to, and
the bus reads that as "answered, and the answer is `None`": the failure disappears. A handler that
only watches ends with `raise`:

```python
seen: list[str] = []


def watch(error: Exception):
    seen.append(type(error).__name__)
    raise error                       # seen, and still an error


payments = new_payments()
payments.add_feature_error_handler(watch)

try:
    payments(CommandPay(amount=500))
    raise AssertionError("the error should have reached the caller")
except CardDeclined:
    pass
assert seen == ["CardDeclined"]
```

With no handler at all, the exception simply reaches the caller.

## A chain: who runs first

The handlers of one chain run in one order: `before` / `after`, then `sequence` (lower first,
default 10), then the order they were registered — the order every extension point uses
([`common/ordering`](../../sincpro_framework/common/ordering/placement.py)). Each one that
re-raises hands the error to the next; the last one that re-raises hands it to the next layer.

```python
order: list[str] = []


def log(error: Exception):
    order.append("log")
    raise error


def answer(error: Exception):
    order.append("answer")
    return ResponsePay(paid=0, error="declined")


payments = new_payments()
payments.add_feature_error_handler(answer, sequence=90)      # registered first, runs last
payments.add_feature_error_handler(log)

assert payments(CommandPay(amount=500), ResponsePay).error == "declined"
assert order == ["log", "answer"]
```

A handler may also raise **another** error: the next handler receives that one. That is how a
chain translates — a driver's error into the domain's — before something answers it.

## Replacing one, switching one off

A handler is named by reference, never by a string, so another module can take its place or turn
it off without editing the code that registered it:

```python
def answer_v2(error: Exception):
    return ResponsePay(paid=0, error="declined, try another card")


payments.add_feature_error_handler(answer_v2, replaces=answer)    # in its place and its order
payments.without_error_handler(log)                               # off, in any of the three chains

assert payments(CommandPay(amount=500), ResponsePay).error == "declined, try another card"
```

- Replacing or switching off something that is not registered works, and the build logs a warning.
- Switching one off **before** it is registered keeps it off when it arrives — a project can turn
  off a handler of a core it installs later.

## Registered after the bus was built

Handlers belong to a generation of the bus, like the handlers they protect: one registered after
the first execution answers from the next execution, which builds a new generation first. A
request already running keeps the chains it started with.

```python
payments = new_payments()
try:
    payments(CommandPay(amount=500))           # built here, with no handler
except CardDeclined:
    pass

payments.add_global_error_handler(lambda error: ResponsePay(paid=0, error="handled late"))

assert payments(CommandPay(amount=500), ResponsePay).error == "handled late"
```

## What is said about a failure

| The failure… | Traces, metrics, GlitchTip | Logs | Outcomes (`on_failure`) |
|---|---|---|---|
| is answered by a handler | recorded where it was raised, before any handler runs | one line: "answered by an error handler" | not announced: nobody saw a failure |
| reaches the caller | recorded where it was raised | logged once, by the outermost bus | `ExecutionFailed` |

An error marked as expected traffic (`bus.ignore_sentry_exceptions(...)`) is logged at info and
kept out of GlitchTip. What kind of failure it is — the caller's, the domain's, a service's — is in
[exceptions](exceptions.md).

## Why it is this way

- **A chain per layer, because a layer knows what its errors mean.** A Feature's handler can turn
  a driver's timeout into a domain answer; only the root knows how the wire wants errors shaped.
  One global chain would have to know every layer.
- **Answer or re-raise — the language's own contract.** A handler is a plain function; returning
  is answering, raising is not. Nothing to learn, and it composes with `try`/`except` in the
  ApplicationService between two layers.
- **The same order as interceptors and hooks.** One algorithm for every extension point, so
  `sequence` and `replaces` mean the same everywhere and a module can place its handler before or
  after another's without numbers guessed against each other.
- **Recorded before it is handled.** An error that a handler turns into an answer still happened:
  the trace and GlitchTip keep it, and only the caller is spared it.
