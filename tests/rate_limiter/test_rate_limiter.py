"""A limiter lets so many calls through in a window of time, and refuses the others or makes them
wait their turn.

What has to hold for a limiter to stand in front of a sale twenty thousand buyers open at once,
and around a gateway that accepts ten calls a second: the call past the limit is refused at once
and not made, with what every wire knows to retry later; a burst across the edge of two windows
is not let in twice; a call that may wait is paced instead of refused, without counting while it
waits; a limiter that only watches refuses nothing and says what it would have refused; each buyer
has a count of its own when asked; replicas sharing a store share the count; the same limiter
guards a block, a function, a coroutine or a call; its count is wherever the running context
keeps its leases; and a store that does not answer never refuses the business.
"""

import asyncio
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from sincpro_framework import CallNext, DataTransferObject, Feature, UseFramework, exceptions
from sincpro_framework.common.failures import refined_failure_kind, retry_after
from sincpro_framework.context import InMemoryContexts
from sincpro_framework.data_layer.store import InMemoryKeyValue, StoreUnavailable
from sincpro_framework.observability.metrics import InMemoryRecorder, metrics
from sincpro_framework.rate_limiter import RateLimited, RateLimiter


class Clock:
    """One time for the limiter, its store and its waits, moved by hand — at the start of a
    window of ten seconds."""

    def __init__(self) -> None:
        self.at = datetime(2026, 11, 19, 12, 0, 0, tzinfo=UTC)
        self.slept: list[float] = []

    def now(self) -> datetime:
        return self.at

    def seconds(self) -> float:
        return self.at.timestamp()

    def passes(self, seconds: float) -> None:
        self.at += timedelta(seconds=seconds)

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.passes(seconds)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(clock: Clock) -> InMemoryKeyValue:
    return InMemoryKeyValue(clock.now)


@pytest.fixture
def recorder() -> Iterator[InMemoryRecorder]:
    recorded = InMemoryRecorder()
    with metrics.using(recorded):
        yield recorded


def limiter(store, clock, limit: int = 2, **options) -> RateLimiter:
    return RateLimiter(
        "purchases",
        limit,
        per=timedelta(seconds=10),
        store=store,
        clock=clock.seconds,
        sleep=clock.sleep,
        **options,
    )


# ── refused at once ────────────────────────────────────────────────────────────────────────


def test_the_call_past_the_limit_is_refused_at_once_and_not_made(store, clock, recorder):
    purchases = limiter(store, clock)
    made: list[int] = []

    purchases.call(made.append, 1)
    purchases.call(made.append, 2)
    clock.passes(4)
    with pytest.raises(RateLimited) as refused:
        purchases.call(made.append, 3)

    assert made == [1, 2]
    # two at once, then one every five seconds: the third is due at 5s
    assert refused.value.retry_after == timedelta(seconds=1)
    assert recorder.totals("sincpro.rate_limiter.refusals") == {
        (("sincpro.enforced", "true"), ("sincpro.limiter", "purchases")): 1
    }


def test_a_burst_across_the_edge_of_two_windows_is_not_let_in_twice(store, clock):
    purchases = limiter(store, clock)
    clock.passes(9)
    purchases.admit()
    purchases.admit()

    clock.passes(1)  # a fixed window would count from nothing here
    with pytest.raises(RateLimited):
        purchases.admit()

    clock.passes(4)  # one every five seconds after the burst
    purchases.admit()


def test_a_burst_of_exactly_the_limit_fits_however_fine_its_turns(store, clock):
    busy = RateLimiter(
        "busy", 1000, per=timedelta(seconds=1), store=store, clock=clock.seconds
    )

    for _ in range(1000):
        busy.admit()  # one every millisecond, all due now: none found due a moment later

    with pytest.raises(RateLimited):
        busy.admit()


def test_a_refused_call_takes_no_place(store, clock):
    purchases = limiter(store, clock)
    purchases.admit()
    purchases.admit()
    for _ in range(3):
        with pytest.raises(RateLimited):
            purchases.admit()

    clock.passes(5)

    purchases.admit()  # due at 5s, as if nobody had insisted


def test_a_refused_call_is_exhausted_and_says_when_to_come_back(store, clock):
    purchases = limiter(store, clock, limit=1)
    purchases.admit()

    with pytest.raises(RateLimited) as refused:
        purchases.admit()

    kind = refined_failure_kind(refused.value)
    assert kind is exceptions.FailureKind.EXHAUSTED
    assert retry_after(refused.value, kind) == timedelta(seconds=10)


# ── waited for ─────────────────────────────────────────────────────────────────────────────


def test_a_call_that_may_wait_is_paced_instead_of_refused(store, clock):
    siat = limiter(store, clock, limit=1, wait=timedelta(seconds=30))
    sent: list[str] = []

    siat.call(sent.append, "F-1")
    siat.call(sent.append, "F-2")

    assert sent == ["F-1", "F-2"]
    assert clock.slept == [pytest.approx(10.0)]  # one call every ten seconds


def test_calls_that_wait_take_their_turns_one_after_another(store, clock):
    siat = limiter(store, clock, limit=1, wait=timedelta(seconds=60))

    for _ in range(4):
        siat.admit()

    assert clock.slept == [pytest.approx(10.0)] * 3  # no turn lost to the ones waiting


def test_a_call_whose_turn_comes_after_its_wait_is_refused_without_waiting(store, clock):
    siat = limiter(store, clock, limit=1, wait=timedelta(seconds=1))
    siat.admit()

    with pytest.raises(RateLimited):
        siat.admit()

    assert clock.slept == []


def test_a_coroutine_waits_its_turn_without_holding_the_event_loop():
    siat = RateLimiter(
        "siat-async", 1, per=timedelta(milliseconds=100), wait=timedelta(seconds=2)
    )
    ticks: list[int] = []

    @siat
    async def send(number: str) -> str:
        return number

    async def scenario() -> list[str]:
        async def ticking() -> None:
            while True:
                ticks.append(1)
                await asyncio.sleep(0.01)

        ticker = asyncio.ensure_future(ticking())
        sent = [await send("F-1"), await send("F-2")]
        ticker.cancel()
        return sent

    started = time.monotonic()
    assert asyncio.run(scenario()) == ["F-1", "F-2"]
    assert time.monotonic() - started < 2
    assert len(ticks) > 1  # the loop ran while F-2 waited


# ── watched before it refuses ──────────────────────────────────────────────────────────────


def test_a_limiter_that_only_watches_refuses_nothing_and_counts_what_it_would(
    store, clock, recorder
):
    purchases = limiter(store, clock, limit=1, enforce=False)

    assert [purchases.call(lambda: "made") for _ in range(3)] == ["made"] * 3
    assert recorder.totals("sincpro.rate_limiter.refusals") == {
        (("sincpro.enforced", "false"), ("sincpro.limiter", "purchases")): 2
    }


# ── whose count ────────────────────────────────────────────────────────────────────────────


def test_each_value_of_its_key_has_a_count_of_its_own(store, clock):
    shop = UseFramework("rate-limited-shop", log_after_execution=False)
    per_buyer = limiter(store, clock, limit=1, by="user_id")

    class CommandBuy(DataTransferObject):
        seat: int

    @shop.feature(CommandBuy)
    class Buy(Feature):
        def execute(self, dto: CommandBuy) -> int:
            return dto.seat

    @shop.interceptor(CommandBuy)
    def one_purchase_per_buyer(dto: CommandBuy, call_next: CallNext[int]) -> int:
        with per_buyer:
            return call_next(dto)

    with shop.context({"user_id": "ana"}):
        assert shop(CommandBuy(seat=1)) == 1
    with shop.context({"user_id": "bob"}):
        assert shop(CommandBuy(seat=2)) == 2
    with shop.context({"user_id": "ana"}), pytest.raises(RateLimited):
        shop(CommandBuy(seat=3))
    assert shop(CommandBuy(seat=4)) == 4  # no user_id: the callers without one share a count
    with pytest.raises(RateLimited):
        shop(CommandBuy(seat=5))


def shared_stores() -> list:
    found = [pytest.param(InMemoryKeyValue(), id="memory")]
    try:
        import fakeredis

        from sincpro_framework.data_layer.store import RedisKeyValue

        found.append(pytest.param(RedisKeyValue(fakeredis.FakeRedis()), id="redis"))
    except ImportError:
        pass
    return found


@pytest.mark.parametrize("shared", shared_stores())
def test_callers_racing_on_one_key_never_get_past_the_limit(shared):
    purchases = RateLimiter("racing", 10, per=timedelta(minutes=1), store=shared)

    def buy(_: int) -> bool:
        try:
            purchases.admit()
        except RateLimited:
            return False
        return True

    with ThreadPoolExecutor(max_workers=16) as buyers:
        admitted = list(buyers.map(buy, range(200)))

    assert sum(admitted) == 10


def test_replicas_sharing_a_store_share_the_count(store, clock):
    one, other = limiter(store, clock), limiter(store, clock)

    one.admit()
    other.admit()

    with pytest.raises(RateLimited):
        one.admit()


def test_a_limiter_built_before_any_execution_counts_where_the_bus_keeps_its_leases():
    purchases = RateLimiter("bus-purchases", 1, per=timedelta(minutes=1))
    kept = InMemoryContexts()
    shop = UseFramework("rate-limited-bus", log_after_execution=False)
    shop.store_context_in(kept)

    class CommandBuy(DataTransferObject):
        seat: int

    @shop.feature(CommandBuy)
    class Buy(Feature):
        def execute(self, dto: CommandBuy) -> int:
            return purchases.call(lambda: dto.seat)

    assert shop(CommandBuy(seat=1)) == 1
    with pytest.raises(RateLimited):
        shop(CommandBuy(seat=2))


# ── any way to call ────────────────────────────────────────────────────────────────────────


def test_the_same_limiter_guards_a_block_a_function_a_coroutine_and_a_call(store, clock):
    purchases = limiter(store, clock, limit=4)

    @purchases
    def buy(seat: int) -> int:
        return seat

    @purchases
    async def buy_later(seat: int) -> int:
        return seat

    with purchases:
        pass
    assert buy(1) == 1
    assert asyncio.run(buy_later(2)) == 2
    assert purchases.call(lambda: 3) == 3
    with pytest.raises(RateLimited):
        buy(4)


# ── what never refuses ─────────────────────────────────────────────────────────────────────


class Unanswering(InMemoryKeyValue):
    def get_many(self, keys: list[str]) -> list[bytes | None]:
        raise StoreUnavailable("the store did not answer")


def test_a_store_that_does_not_answer_never_refuses_a_call(clock):
    purchases = limiter(Unanswering(clock.now), clock, limit=1)

    assert [purchases.call(lambda: "through") for _ in range(3)] == ["through"] * 3


def test_a_limiter_that_could_never_work_is_refused_when_built():
    with pytest.raises(exceptions.ProgrammingError, match="limit=0"):
        RateLimiter("never", 0, per=timedelta(seconds=1))
    with pytest.raises(exceptions.ProgrammingError, match="no time"):
        RateLimiter("never", 1, per=timedelta(0))
    with pytest.raises(exceptions.ProgrammingError, match="negative"):
        RateLimiter("never", 1, per=timedelta(seconds=1), wait=timedelta(seconds=-1))
