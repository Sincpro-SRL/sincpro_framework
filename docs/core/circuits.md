# Circuits: stop calling what keeps failing, try again once it may be back

A dependency that stops answering — SIAT, a payment gateway, another service — turns every call into
a wait for its timeout, and a caller under load ties up every thread waiting. A circuit counts the
calls that did not get an answer; after enough of them in a row it **opens**: calls fail at once,
without being made, for a while. Then it lets **one** call through to try: an answer closes it, a
failure opens it again.

A circuit is part of the core and needs no library. Its state is kept where the context keeps its
leases — the process's memory, or the store every replica shares — and an open one raises
`CircuitOpen`: `UNAVAILABLE`, with the seconds left as its `retry_after` — never under a second —,
which every wire (503 and `Retry-After` over REST), a pipeline's `Retry` and a queue already know
how to wait for.

Every block on this page runs, in order, in `tests/docs/test_persistence_guide.py`.

## Opening, waiting, trying again

```python
from datetime import UTC, datetime, timedelta

from sincpro_framework import exceptions
from sincpro_framework.circuits import Circuit, CircuitOpen, CircuitState
from sincpro_framework.store import InMemoryKeyValue

at = [datetime(2026, 10, 24, tzinfo=UTC)]               # one clock, moved by hand
store = InMemoryKeyValue(now=lambda: at[0])


class GatewayDown(exceptions.ServiceUnavailableError):
    pass


def send() -> str:
    raise GatewayDown("the gateway did not answer")


gateway = Circuit(
    "gateway",
    failures=3,                                         # in a row
    open_for=timedelta(seconds=30),
    store=store,                                        # the context's when not given
    clock=lambda: at[0].timestamp(),
)

for _ in range(3):
    try:
        gateway.call(send)
    except GatewayDown:
        pass

try:
    gateway.call(send)                                  # not made: it fails at once
except CircuitOpen as refused:
    assert refused.retry_after == timedelta(seconds=30)
assert gateway.state() is CircuitState.OPEN

at[0] += timedelta(seconds=31)                          # its time passed
assert gateway.state() is CircuitState.HALF_OPEN
assert gateway.call(lambda: "back") == "back"           # the one call let through answered
assert gateway.state() is CircuitState.CLOSED
```

| State | A call | What moves it |
|---|---|---|
| `CLOSED` | is made | `failures` in a row that count → `OPEN`; an answer clears the count |
| `OPEN` | fails at once with `CircuitOpen` — not made | `open_for` passes → `HALF_OPEN` |
| `HALF_OPEN` | one is made, to try; the others fail at once, told to come back in a second | it answers → `CLOSED`; it fails → `OPEN` again |

`reset()` closes it by hand — what an operator does once the dependency is known back.

The call let through half-open keeps the others out for `probe_for` — `open_for` unless said. Give
it more than the longest call takes (`Circuit("siat", probe_for=timedelta(minutes=2))`): a probe
still waiting when it lapses lets a second one in. Either way an answer that arrives after another
probe opened the circuit again never closes it: it stays open, then half-open, and only a call
that tried closes it.

`retry_after` is what is left of the time open, and never under a second: the time left is the
writer's clock against the reader's, and a store that keeps whole-second TTLs (Memcached) can
outlive it by a moment — a caller is never told to retry at once.

## Any way to call: a block, a function, a method, a coroutine, a generator — or the adapter's own

One circuit, built once, guards whatever shape the code has. Each way is the same circuit:

```python
import asyncio

siat = Circuit("siat-docs", store=InMemoryKeyValue())


with siat:                                              # a block
    issued = "F-1"


@siat                                                   # a function
def issue(number: str) -> str:
    return number


@siat                                                   # a coroutine
async def issue_later(number: str) -> str:
    return number


class SiatClient:                                       # the usual way: the adapter takes it
    def __init__(self, circuit: Circuit) -> None:
        self.circuit = circuit

    def issue(self, number: str) -> str:
        with self.circuit:
            return number                               # the SOAP call goes here


@siat                                                   # a generator: guarded while iterated
def pages():
    yield "page 1"
    yield "page 2"


assert issue("F-2") == "F-2"
assert asyncio.run(issue_later("F-3")) == "F-3"
assert SiatClient(siat).issue("F-4") == "F-4"
assert siat.call(issue, "F-5") == "F-5"
assert list(pages()) == ["page 1", "page 2"]
```

A generator — sync or async — is admitted at its first `next` and settled when it ends, so a
failure halfway through the pages counts; one abandoned early is a call that never ended (below).

A `with` block settles, when it is left, the admission **its own** circuit made for it — blocks of
two circuits left out of order each settle their own. Left in another context than it was entered
(an `ExitStack` closed elsewhere, a dependency with `yield` resumed in another task), it settles
nothing and says so in the logs; whatever the block raised goes on as it was. Nothing that goes
wrong keeping a circuit ever replaces what the call raised.

The adapter is registered as any dependency — `add_dependency("siat", SiatClient(Circuit("siat")))`
in `infrastructure/dependencies.py` — and every Feature that uses it shares its one circuit. A
circuit guards the calls **out** of a process: an adapter, a bus hosted elsewhere. A Feature never
holds one; it raises what it raises, and whoever orchestrates it — a pipeline's `Retry`, a queue —
decides to try again.

## Where its state lives: the context's

A circuit is built at boot, with its adapter, before any execution runs. At every call it reads
where the **running** context keeps its leases: the store of the bus it runs in, else the
process's (`store_context_in(redis)`), else this process's memory — the same rule `bus.cache` and
`bus.idempotency` keep by. Nothing about the store is said where the circuit is built:

```python
from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.context import InMemoryContexts

payments = Circuit("payments-docs", failures=1)         # no store: the context's
kept = InMemoryContexts()                               # KeyValueContexts(RedisKeyValue(...)) on replicas
shop = UseFramework("circuits-shop", log_after_execution=False)
shop.store_context_in(kept)


class CommandCharge(DataTransferObject):
    amount: int


@shop.feature(CommandCharge)
class Charge(Feature):
    def execute(self, dto: CommandCharge) -> None:
        payments.call(send)


for refused_with in (GatewayDown, CircuitOpen):
    try:
        shop(CommandCharge(amount=10))
    except refused_with:
        pass

assert kept.leases().store.get("sincpro:circuit:payments-docs:open") is not None
```

| The context is kept in | The circuit is |
|---|---|
| `InMemoryContexts`, or no store at all | this process's: each replica learns by itself |
| `KeyValueContexts` over Redis, Valkey, Memcached, DynamoDB | every replica's: they open and close together |

It stands on the store's own operations, so every store has it: **open** is a key written with the
time it is open for — it lapses by itself into half-open, nobody has to close it —; the call let
through half-open is whoever wins an atomic `add`; the failures are a counter.

**A store that does not answer never blocks a call.** The circuit lets calls through unguarded
for 30 seconds, says so in the logs, and tries the store again: it protects the dependency, not
the store. (Leases, drafts and pipeline runs do the opposite and fail: an answer from memory there
would be two holders of one lease.)

## What counts as a failure

By default, a failure that says the dependency is **not answering**: `UNAVAILABLE`,
`UNKNOWN_OUTCOME`, `EXHAUSTED` (it asked to be left alone) and anything that declares no kind — a
client's own `ConnectionError`, a crash. An error that **is** an answer — invalid, not found, a
rule of the business, a conflict — never counts: the dependency is up, it said no.

```python
from sincpro_framework.ddd.exceptions import DomainError


class InvoiceRefused(DomainError):
    pass


def refuse() -> str:
    raise InvoiceRefused("the invoice does not balance")


strict = Circuit("strict-docs", failures=1, store=InMemoryKeyValue())
for _ in range(3):
    try:
        strict.call(refuse)
    except InvoiceRefused:
        pass
assert strict.state() is CircuitState.CLOSED            # answers, never failures

picky = Circuit(
    "picky-docs",
    failures=1,
    store=InMemoryKeyValue(),
    counts=lambda error: isinstance(error, InvoiceRefused),   # the project says otherwise
)
try:
    picky.call(refuse)
except InvoiceRefused:
    pass
assert picky.state() is CircuitState.OPEN
```

**A call that never ended is neither.** Cancelled (`asyncio.CancelledError`), interrupted
(`KeyboardInterrupt`), a generator closed before its end: nothing is counted and nothing cleared —
a probe it held is let go for the next call to try. A deadline belongs **inside** the guarded call,
where its `TimeoutError` counts; `asyncio.wait_for(guarded(), 5)` cancels the call from outside,
and a dependency that hangs past it never opens the circuit:

```python
hung_gateway = Circuit("hung-docs", failures=2, store=InMemoryKeyValue())


@hung_gateway
async def wait_on_the_gateway() -> None:
    await asyncio.sleep(0.05)                           # stands for the gateway hanging


@hung_gateway
async def wait_with_its_deadline() -> None:
    async with asyncio.timeout(0.01):                   # the deadline inside: a failure
        await asyncio.sleep(0.05)


for _ in range(2):
    try:
        asyncio.run(asyncio.wait_for(wait_on_the_gateway(), 0.01))   # cancelled from outside
    except TimeoutError:
        pass
assert hung_gateway.state() is CircuitState.CLOSED                   # never counted

for _ in range(2):
    try:
        asyncio.run(wait_with_its_deadline())
    except TimeoutError:
        pass
assert hung_gateway.state() is CircuitState.OPEN
```

**"In a row" is per call, not a strict sequence under concurrency.** Calls that end in failure
add one each; a call that answers clears the count only when it saw failures counted as it was let
through. Many calls at once against a dependency that half answers can therefore open it a little
before or after exactly `failures` failures in a row — a threshold, not a ledger.

## A bus hosted by another service: one when asked for

A bus whose context another service hosts makes every call, as it always did — **no circuit
unless one is asked for**. It is asked for the two ways an address is given, and the code wins:

| Where | Written | The circuit |
|---|---|---|
| the address — `context_map:`, `SINCPRO_CONTEXT_MAP`, or `hosted_by` | `grpc://billing-svc:50051?circuit=on` | the address's own: 5 calls in a row, 30 seconds open |
| | `…?circuit=on&circuit_failures=3&circuit_open_for=10` | tuned — tuning a circuit that is off is refused: it would look guarded and not be |
| code | `billing.hosted_by(address, circuit=True)` | the address's own, whatever the address says |
| | `billing.hosted_by(address, circuit=Circuit("billing", failures=3))` | the project's: its own `counts=`, its own name, its own store |
| | `billing.hosted_by(address, circuit=False)` | none, whatever the address says |

Once on, after the calls in a row that did not reach the context, calls fail at once with the same
`ContextUnavailable` — its `retry_after` what is left of the time open — instead of each waiting out
the deadline. The address's own circuit counts only what **this process's transport** raised: the
context not reached, or no answer back. Whatever the hosting service raised is its answer — even
its own `CircuitOpen` in front of SIAT, an outcome it does not know, a context **it** could not
reach — and arrives as itself, its `retry_after` with it: the host is up, and an outage behind it
never cuts every caller off. The call let through half-open keeps the others out for two of the
address's deadlines, so a probe still waiting never lets a second one in.

A `Circuit` handed in code counts what its `counts=` says — by default, every `UNAVAILABLE`,
including the host's own answers. For the address's rule with a circuit of the project's, hand it
`counts=is_unreached`:

```python
from sincpro_framework.remote_execution import is_unreached

billing_circuit = Circuit("billing", failures=3, counts=is_unreached)
```

```python
from sincpro_framework.common.transport.addresses import HostedAt

assert HostedAt.parse("grpc://billing-svc:50051").circuit is False          # as always
guarded = HostedAt.parse("grpc://billing-svc:50051?circuit=on")
assert (guarded.circuit, guarded.circuit_failures, guarded.circuit_open_for) == (True, 5, 30.0)
tuned = HostedAt.parse(
    "grpc://billing-svc:50051?circuit=on&circuit_failures=3&circuit_open_for=10"
)
assert (tuned.circuit_failures, tuned.circuit_open_for) == (3, 10.0)
```

## What the observability says of it

`CircuitOpen` is a fault of the service — a dependency it needs is down — so, as every
`ServiceUnavailableError`, each call it refuses is an error report, an `error` log line and
`sincpro.fault="true"` on the metrics: an outage shows as one. The circuit says once, as a
`warning`, when it opens and when it closes. A project that wants the calls an open circuit refused
counted but not reported says so on its bus — `bus.ignore_sentry_exceptions(CircuitOpen)` — and
they become traffic: an `info` line, `sincpro.fault="false"`.

## What it is not

- **Not a retry.** It stops calling; who calls again is whoever orchestrates — a pipeline's `Retry`
  waits the `retry_after` it raises, a queue redelivers. Retry in **one** layer: an adapter that
  retries three times under a pipeline that retries three times under a queue that delivers five
  times makes 45 calls to a service that is down.
- **Not a timeout.** How long one call may take is the client's — its socket timeout, the
  address's `timeout`. A circuit decides whether to make the call at all.
- **Not a lock.** One name, many callers at once; a lease is one holder at a time
  ([leases.md](leases.md)).
