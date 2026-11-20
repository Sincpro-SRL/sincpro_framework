# Rate limits: so many calls a second, and the others refused or made to wait

A sale that opens at noon gets twenty thousand buyers in its first second, and a bot that buys for
a reseller asks a hundred times a second. Going the other way, SIAT or a payment gateway accepts
ten calls a second from this service and answers the eleventh with an error. A `RateLimiter`
lets so many calls through every so long; past that, a call is **refused** — not made, failing at
once with `RateLimited` — or, when it may, **waits its turn**.

`RateLimited` is `EXHAUSTED`, with the seconds until there is room as its `retry_after` — never
under a second —, which every wire (429 and `Retry-After` over REST), a pipeline's `Retry` and a
queue already know how to wait for. A limiter is part of the core and needs no library; it has
the shape of a [circuit](circuit-breaker.md): built once, used as a block, a decorator or a call,
its state stored where the context stores its leases.

Every block on this page runs, in order, in `tests/docs/test_persistence_guide.py`.

## Where it goes

| What is limited | Where | Why there |
|---|---|---|
| Every request, or each IP | the edge — Kong, Cloudflare | before a request costs anything; behind a proxy only the edge knows the real IP |
| **Each authenticated caller**: five purchases a minute per buyer | an [interceptor](interceptors.md) in front of the use case, `by="user_id"` | only the service knows who the caller is once it authenticated |
| **A dependency's quota**: ten calls a second to SIAT | the adapter that calls it, `wait=` | this service's own calls are paced, not failed |

A rule of the business is not a rate: "at most four tickets per buyer for a show" holds whenever
the tickets are bought, so it is checked in the write that sells them, as any rule of the aggregate.

### Where it is written in a project

A limiter is wired where a circuit is, never inside a use case:

| A limit on | File | Written as |
|---|---|---|
| a dependency's quota | `adapters/siat.py`, built in `infrastructure/dependencies.py` | the adapter takes it in its constructor, as a `CircuitBreaker`: `add_dependency("siat", SiatClient(CircuitBreaker("siat"), RateLimiter("siat", 10, per=timedelta(seconds=1), wait=timedelta(seconds=5))))` — every Feature that uses the adapter shares its one limit |
| each caller | the bounded context's `__init__.py`, after `from . import services` | an interceptor that opens the block — beside `auth.on(...)` and the error handlers, step 3 of the bootstrap |

A Feature never holds a limiter: it raises what it raises, and the limit is said once, where the
calls go in or out.

## Refused at once: in front of a use case

```python
from datetime import UTC, datetime, timedelta

from sincpro_framework import CallNext, DataTransferObject, Feature, UseFramework
from sincpro_framework.data_layer.store import InMemoryKeyValue
from sincpro_framework.rate_limiter import RateLimited, RateLimiter

at = [datetime(2026, 11, 19, 12, 0, tzinfo=UTC)]        # one clock, moved by hand
store = InMemoryKeyValue(now=lambda: at[0])

per_buyer = RateLimiter(
    "purchases",
    2,                                                  # calls
    per=timedelta(minutes=1),                           # every minute
    by="user_id",                                       # a count for each user
    store=store,                                        # the context's when not given
    clock=lambda: at[0].timestamp(),
)

shop = UseFramework("rate-limited-shop", log_after_execution=False)


class CommandBuy(DataTransferObject):
    seat: int


@shop.feature(CommandBuy)
class Buy(Feature):
    def execute(self, dto: CommandBuy) -> int:
        return dto.seat


@shop.interceptor(CommandBuy)
def two_purchases_a_minute(dto: CommandBuy, call_next: CallNext[int]) -> int:
    with per_buyer:
        return call_next(dto)


with shop.context({"user_id": "ana"}):
    assert shop(CommandBuy(seat=1)) == 1
    assert shop(CommandBuy(seat=2)) == 2
    try:
        shop(CommandBuy(seat=3))                        # not run: refused at once
    except RateLimited as refused:
        assert refused.retry_after == timedelta(seconds=30)
with shop.context({"user_id": "bob"}):
    assert shop(CommandBuy(seat=3)) == 3                # a count of his own

at[0] += timedelta(seconds=30)
with shop.context({"user_id": "ana"}):
    assert shop(CommandBuy(seat=4)) == 4                # her next turn came
```

Callers whose context holds no value for `by` share one count; with no `by` every caller shares
it. A refused call writes nothing, so a caller that keeps asking gets its turn when it comes, and
not later.

## Waited for: a dependency with a quota

A call to SIAT that fails because this service went too fast is a failure this service caused.
With `wait=` a call waits its turn instead — up to that long — and is refused only when its turn
comes later. A coroutine waits without holding the event loop:

```python
import asyncio

slept: list[float] = []


def sleeping(seconds: float) -> None:                  # what a synchronous call waits with
    slept.append(seconds)
    at[0] += timedelta(seconds=seconds)


siat = RateLimiter(
    "siat",
    10,
    per=timedelta(seconds=1),                           # ten a second
    wait=timedelta(seconds=5),                          # a call waits up to five seconds
    store=store,
    clock=lambda: at[0].timestamp(),
    sleep=sleeping,                                     # time.sleep when not given
)


class SiatClient:                                       # the adapter takes it, as a circuit
    def __init__(self, rate_limiter: RateLimiter) -> None:
        self.rate_limiter = rate_limiter

    def send(self, invoice: str) -> str:
        with self.rate_limiter:
            return invoice                              # the SOAP call goes here


client = SiatClient(siat)
sent = [client.send(f"F-{n}") for n in range(12)]

assert len(sent) == 12                                  # none failed
assert slept == [0.1, 0.1]                              # ten at once, then one every 0.1s
```

A coroutine awaits its turn on the event loop's own time — `asyncio.sleep` — whatever `sleep` says:

```python
siat_async = RateLimiter("siat-async", 10, per=timedelta(seconds=1), wait=timedelta(seconds=5))


@siat_async
async def send_later(invoice: str) -> str:
    return invoice


async def send_all() -> list[str]:
    return [await send_later(f"F-{n}") for n in range(11)]   # the eleventh awaits 0.1s


assert len(asyncio.run(send_all())) == 11
```

## Watched before it refuses

A limit set too low refuses customers. Before it refuses anybody, `enforce=False` counts the calls
it would refuse and lets them through:

```python
from sincpro_framework.observability.metrics import InMemoryRecorder, metrics

watching = RateLimiter("checkout", 1, per=timedelta(minutes=1), enforce=False, store=store)

recorder = InMemoryRecorder()
with metrics.using(recorder):
    assert [watching.call(lambda: "made") for _ in range(3)] == ["made"] * 3

assert recorder.totals("sincpro.rate_limiter.refusals") == {
    (("sincpro.enforced", "false"), ("sincpro.limiter", "checkout")): 2
}
```

Every refusal — made, or only watched — is counted on `sincpro.rate_limiter.refusals`, labelled
by `sincpro.limiter` and `sincpro.enforced`; one that is only watched also writes a line in the
log. Once the numbers say the limit is right, `enforce` goes back to its default.

## How it counts

It keeps one number per key: **the moment the next call is due** — GCRA, the generic cell rate
algorithm redis-cell implements, the token bucket Stripe limits with. `limit` calls may come at
once; after them, one every `per / limit`. An admitted call moves that moment on, and writes it
only if nobody wrote it meanwhile — `add` for the first, `replace` compared with what was read
after it, the operations a lease writes with, so every store of the framework has them. Of many
callers racing for the last place, exactly one gets it; the others read again.

What that gives, against the usual counters:

| | A counter per window | This |
|---|---|---|
| A burst across the edge of two windows | up to twice the limit | never more than the limit |
| A refused call | counted | writes nothing |
| A call that waits | counted again on each try | takes no place while it waits |
| What it stores | a counter per window | one number per key |

A count every caller shares — no `by` — is one key that every replica compares and writes: under
heavy concurrency the writers retry. That is why a ceiling for the whole service belongs at the
edge, and a limiter here is per caller or per dependency.

## Where its state lives: the context's

A limiter is built at boot, before any execution runs. At every call it reads where the
**running** context stores its leases: the store of the bus it runs in, else the process's
(`store_context_in(redis)`), else this process's memory — the rule a circuit, `bus.cache` and
`bus.idempotency` follow.

| The context is stored in | The limit holds for |
|---|---|
| `InMemoryContexts`, or no store at all | this process: each replica allows the limit on its own |
| `KeyValueContexts` over Redis, Valkey, Memcached, DynamoDB | the whole service: every replica shares it |

**A store that does not answer never refuses a call.** The limiter lets calls through uncounted for
30 seconds, says so in the logs, and tries the store again: a limit that cannot be counted is no
reason to turn the business away.

## Reference

| | |
|---|---|
| `RateLimiter(name, limit, per, by=None, wait=0, enforce=True, store=None, prefix="sincpro:rate_limiter", clock=time.time, sleep=time.sleep)` | `name` is the limit — the same name is the same count for every caller sharing the store |
| `limiter.admit()` | admits this call — at once, after waiting up to `wait`, or `RateLimited` |
| `with limiter:` · `async with limiter:` · `@limiter` · `limiter.call(f, …)` | admitted before it runs; a coroutine awaits its turn |
| `RateLimited(name, retry_after)` | a `ClientError`, `EXHAUSTED`: 429 with `Retry-After` over REST, run again by a queue and a pipeline's `Retry` |
| `sincpro.rate_limiter.refusals` | counter: `sincpro.limiter`, `sincpro.enforced` |
