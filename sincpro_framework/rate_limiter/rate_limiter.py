"""`RateLimiter`: at most so many calls in a window of time — for every caller, or for each value
of a key of the context — and the others refused, or made to wait their turn.

    purchases = RateLimiter("purchases", 100, per=timedelta(seconds=1))           every caller
    per_buyer = RateLimiter("buyer", 5, per=timedelta(minutes=1), by="user_id")    each user
    siat = RateLimiter("siat", 10, per=timedelta(seconds=1), wait=timedelta(seconds=5))

    with purchases:                                 a block
        ...

    @per_buyer                                      a function, a method, a coroutine
    def buy(dto): ...

    siat.call(client.send, invoice)                 a call

**Refused or waited for.** Calls coming in — a buyer, a bot — are refused at once past the limit
with `RateLimited`: `EXHAUSTED` on every wire, with the seconds until there is room as its
`retry_after` — 429 and `Retry-After` over REST, run again later by a queue and by a pipeline's
`Retry`. Calls going out to a dependency with a quota of its own — SIAT, a payment gateway — are
better paced than failed: with `wait=` a call waits its turn, up to that long, and is refused only
if its turn comes later; a coroutine waits without holding the event loop.

**Exact, with one value per key.** GCRA — the generic cell rate algorithm of redis-cell, the token
bucket Stripe limits with, kept as one number: the moment the next call is due. `limit` calls may
come at once; after them, one every `per / limit`. An admitted call moves that moment on, written
only if nobody wrote it meanwhile (`add`, then `replace` — what a lease writes with, so every store
of the framework has it); a refused call writes nothing, a waiting call takes no place while it
waits, and no edge between two windows lets in twice the limit. A count every caller shares is one
key every replica compares and writes: under heavy concurrency writers retry — a ceiling for the
whole service belongs at the edge (Kong); a limiter here is per caller, or per dependency.

**Watched before it refuses.** `enforce=False` counts and reports the calls it would refuse —
`sincpro.rate_limiter.refusals` with `sincpro.enforced="false"`, and a line in the log — and
lets them through: how a limit is tried on real traffic before it is turned on.

**Where the counts live is the context's**, as for a circuit: at every call it reads where the
running context stores its leases — the process's memory, or the Redis, Valkey, Memcached or
DynamoDB every replica shares. `store=` names one for a test or a script.

**A store that does not answer never refuses a call.** The calls go through, uncounted, for a
while — the store's own `StoreBypass` — and the store is tried again: a limit that cannot be
counted is no reason to turn the business away.
"""

import asyncio
import functools
import inspect
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any, TypeVar, cast

from sincpro_framework import exceptions
from sincpro_framework.context import use_context
from sincpro_framework.context.entrypoint.resolution import current_store, key_value_store_of
from sincpro_framework.data_layer.store import KeyValueStore, StoreBypass, StoreUnavailable
from sincpro_framework.observability.metrics import Instrument, InstrumentKind, metrics
from sincpro_framework.sincpro_logger import logger

STORE_BYPASS = timedelta(seconds=30)
"""How long calls go through uncounted after the store did not answer."""
LEAST_RETRY_AFTER = timedelta(seconds=1)
"""The least `retry_after` a refused call is told — never to retry at once."""
WITHOUT_KEY = "-"
"""Where the calls go whose context holds no value for `by`: they share one count."""
MICROSECONDS = 1_000_000
"""Time is counted in whole microseconds: added in floating seconds since the epoch, a burst
drifts by tenths of a microsecond, and a call due exactly now would be found due a moment later."""

REFUSALS = Instrument(
    name="sincpro.rate_limiter.refusals",
    kind=InstrumentKind.COUNTER,
    unit="{call}",
    description="Every call past a limit — refused, or let through by a limiter that only watches",
    label_keys=("sincpro.limiter", "sincpro.enforced"),
)
"""Refusals rising is a caller asking too much — or a limit set too low, seen while it watches."""

F = TypeVar("F", bound=Callable[..., Any])


class RateLimited(exceptions.ClientError):
    """The caller asked more often than its limit allows: the call was not made. Try again after
    `retry_after` — a second at least."""

    failure_kind = exceptions.FailureKind.EXHAUSTED

    name: str
    retry_after: timedelta
    """Declared, so it is a `timedelta` again once rebuilt across a remote boundary."""

    def __init__(self, name: str, retry_after: timedelta) -> None:
        """Refused with the time until there is room, floored at `LEAST_RETRY_AFTER`.

        RateLimited("purchases", timedelta(0)).retry_after   ->  timedelta(seconds=1)
        """
        retry_after = max(retry_after, LEAST_RETRY_AFTER)
        super().__init__(
            f"rate limit {name} reached — the call was not made; "
            f"try again in {retry_after.total_seconds():.0f}s"
        )
        self.name = name
        self.retry_after = retry_after


class RateLimiter:
    """At most `limit` calls every `per` — counted for everyone, or per value of the context's
    key `by` — where the running context stores its leases."""

    def __init__(
        self,
        name: str,
        limit: int,
        per: timedelta,
        by: str | None = None,
        wait: timedelta = timedelta(0),
        enforce: bool = True,
        store: KeyValueStore | None = None,
        prefix: str = "sincpro:rate_limiter",
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """`name` is the limit — the same name is the same count for every caller sharing the
        store; `by` a key of the running context (`user_id`, `tenant_id`) that gives each of its
        values a count of its own; `wait` how long a call may wait its turn before it is refused;
        `enforce=False` only watches; `store` pins one instead of the context's; `clock` is
        seconds since the epoch and `sleep` how a synchronous call waits — both a test's own; a
        coroutine awaits `asyncio.sleep`, on the event loop's own time.

            RateLimiter("purchases", 100, per=timedelta(seconds=1))
            RateLimiter("purchases", 0, per=timedelta(seconds=1))
                ->  ProgrammingError("rate limit purchases: limit=0 would refuse every call …")
        """
        if limit < 1:
            raise exceptions.ProgrammingError(
                f"rate limit {name}: limit={limit} would refuse every call — give 1 or more"
            )
        if per <= timedelta(0):
            raise exceptions.ProgrammingError(
                f"rate limit {name}: per={per} is no time at all — give a positive time"
            )
        if wait < timedelta(0):
            raise exceptions.ProgrammingError(
                f"rate limit {name}: wait={wait} is negative — give no time, or some"
            )
        self.name = name
        self.limit = limit
        self.per = per
        self.by = by
        self.wait = wait
        self.enforce = enforce
        self.prefix = prefix
        self.clock = clock
        self.sleep = sleep
        self._store = store
        self._period = round(per.total_seconds() * MICROSECONDS)
        self._interval = max(1, self._period // limit)
        self._bypass = StoreBypass(STORE_BYPASS, clock)

    def store(self) -> KeyValueStore:
        """Where the counts are: the store given, else the `KeyValueStore` of the running
        context — read at every call, so a limiter built before any execution follows the bus it
        runs in."""
        if self._store is not None:
            return self._store
        return key_value_store_of(current_store())

    def _attempt(self, give_up_at: float) -> float:
        """One try at a place: 0 when admitted, the seconds to pause before the next try when
        it may still wait, `RateLimited` when it may not.

            limit 2 per 10s, both taken at 0s, asked at 4s
                ->  due at 15s, admitted from 5s: refused, 1s to wait

        1. The store did not answer a moment ago: admitted, uncounted.
        2. When the next call is due for this key — now when nothing is stored, or when it is
           past —; this call moves it on by `per / limit`, and fits once that is within `per`.
        3. Not yet: only watching (`enforce=False`) — reported and admitted; else the pause
           until it fits, or refused — counted — when that is past `give_up_at`. Nothing is
           written either way.
        4. It fits: the new moment written, compared with what was read — another caller wrote
           first, and it is read again.
        Final: a store that stops answering admits the call and is bypassed a while.
        """
        if self._bypass.is_open():
            return 0.0
        value = use_context().get(self.by) if self.by is not None else None
        key = f"{self.prefix}:{self.name}:{WITHOUT_KEY if value is None else value}"
        try:
            store = self.store()
            while True:
                seconds = self.clock()
                now = round(seconds * MICROSECONDS)
                held = store.get(key)
                due = max(int(held) if held is not None else now, now) + self._interval
                pause = (due - self._period - now) / MICROSECONDS
                if pause > 0:
                    if not self.enforce:
                        metrics.emit(
                            REFUSALS,
                            1,
                            {"sincpro.limiter": self.name, "sincpro.enforced": "false"},
                        )
                        logger.info(
                            f"rate limit {self.name}: past its limit — let through, it only watches"
                        )
                        return 0.0
                    if seconds + pause > give_up_at:
                        metrics.emit(
                            REFUSALS,
                            1,
                            {"sincpro.limiter": self.name, "sincpro.enforced": "true"},
                        )
                        raise RateLimited(self.name, timedelta(seconds=pause))
                    return pause
                written = str(due).encode()
                lasts = timedelta(microseconds=due - now)
                if held is None:
                    taken = store.add(key, written, lasts)
                else:
                    taken = store.replace(key, held, written, lasts)
                if taken:
                    return 0.0
        except StoreUnavailable as error:
            self._bypass.trip()
            logger.warning(
                f"rate limit {self.name}: its store did not answer ({error}); calls go through "
                f"uncounted for {STORE_BYPASS.total_seconds():.0f}s"
            )
            return 0.0

    def admit(self) -> None:
        """Admit this call — at once, after waiting its turn up to `wait`, or `RateLimited`.

        purchases.admit()   ->  None                                   # within the limit
        purchases.admit()   ->  RateLimited("purchases", 1s)           # past it
        siat.admit()        ->  None, after 0.1s waiting its turn      # 10 a second, wait=5s
        """
        give_up_at = self.clock() + self.wait.total_seconds()
        while (pause := self._attempt(give_up_at)) > 0:
            self.sleep(pause)

    async def _admit_awaiting(self) -> None:
        """`admit` for a coroutine: its turn awaited, never held on the event loop; the store
        is read synchronously, as a circuit reads it."""
        give_up_at = self.clock() + self.wait.total_seconds()
        while (pause := self._attempt(give_up_at)) > 0:
            await asyncio.sleep(pause)

    def call(self, function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """`function(*args, **kwargs)` once admitted — `RateLimited` and not called otherwise."""
        self.admit()
        return function(*args, **kwargs)

    def __enter__(self) -> "RateLimiter":
        """A block admitted — or `RateLimited` before it runs."""
        self.admit()
        return self

    def __exit__(self, kind: Any, error: BaseException | None, trace: Any) -> None:
        """Nothing to settle: a call counts when it is admitted, whatever it then does."""

    async def __aenter__(self) -> "RateLimiter":
        """`async with` — admitted as `with`, its turn awaited."""
        await self._admit_awaiting()
        return self

    async def __aexit__(self, kind: Any, error: BaseException | None, trace: Any) -> None:
        """Nothing to settle, as `__exit__`."""

    def __call__(self, function: F) -> F:
        """A function, a method or a coroutine function admitted at each call — a coroutine
        when it starts running, its turn awaited; a generator when it is created."""
        if inspect.iscoroutinefunction(function):

            @functools.wraps(function)
            async def admitted_coroutine(*args: Any, **kwargs: Any) -> Any:
                await self._admit_awaiting()
                return await function(*args, **kwargs)

            return cast(F, admitted_coroutine)

        @functools.wraps(function)
        def admitted(*args: Any, **kwargs: Any) -> Any:
            self.admit()
            return function(*args, **kwargs)

        return cast(F, admitted)
