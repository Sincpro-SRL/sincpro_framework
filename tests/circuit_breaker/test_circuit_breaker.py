"""A circuit stops calling a dependency that keeps failing, and tries it again once it may be back.

What has to hold for a circuit to be trusted in front of SIAT, a payment gateway or another
service: it opens after the failures that say the dependency is not answering — never after an
answer it did not like —, an open one fails at once with what every wire knows to retry later,
one call alone tries it again, replicas sharing a store agree, the same circuit guards a block, a
function, a method or a coroutine, its state is wherever the running context keeps its leases,
and a store that does not answer never blocks a call.
"""

import asyncio
import contextvars
from datetime import UTC, datetime, timedelta

import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework, exceptions
from sincpro_framework.circuit_breaker import CircuitBreaker, CircuitOpen, CircuitState
from sincpro_framework.common.failures import refined_failure_kind, retry_after
from sincpro_framework.context import ContextStore, InMemoryContexts
from sincpro_framework.data_layer.store import (
    InMemoryKeyValue,
    KeyValueStore,
    StoreUnavailable,
)
from sincpro_framework.ddd.exceptions import DomainError


class Clock:
    """One time for the circuit and its store, moved by hand."""

    def __init__(self) -> None:
        self.at = datetime(2026, 10, 24, tzinfo=UTC)

    def now(self) -> datetime:
        return self.at

    def seconds(self) -> float:
        return self.at.timestamp()

    def passes(self, seconds: float) -> None:
        self.at += timedelta(seconds=seconds)


class Down(exceptions.ServiceUnavailableError):
    pass


class Refused(DomainError):
    pass


def down() -> None:
    raise Down("the gateway did not answer")


def refused() -> None:
    raise Refused("the invoice does not balance")


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(clock: Clock) -> InMemoryKeyValue:
    return InMemoryKeyValue(clock.now)


def circuit(store: KeyValueStore, clock: Clock, **options) -> CircuitBreaker:
    return CircuitBreaker("gateway", store=store, clock=clock.seconds, **options)


def fail(guarded: CircuitBreaker, times: int, with_=down) -> None:
    for _ in range(times):
        with pytest.raises(Exception):
            guarded.call(with_)


def test_it_opens_after_the_failures_in_a_row_and_fails_at_once_without_calling(store, clock):
    guarded = circuit(store, clock, failures=3, open_for=timedelta(seconds=30))
    calls: list[str] = []

    fail(guarded, 2)
    assert guarded.state() is CircuitState.CLOSED
    fail(guarded, 1)

    with pytest.raises(CircuitOpen) as refused_call:
        guarded.call(lambda: calls.append("called"))

    assert calls == [] and guarded.state() is CircuitState.OPEN
    assert refined_failure_kind(refused_call.value) is exceptions.FailureKind.UNAVAILABLE
    assert retry_after(refused_call.value, exceptions.FailureKind.UNAVAILABLE) == timedelta(
        seconds=30
    )


def test_an_answer_between_failures_starts_the_count_again(store, clock):
    guarded = circuit(store, clock, failures=3)

    fail(guarded, 2)
    assert guarded.call(lambda: "answered") == "answered"
    fail(guarded, 2)

    assert guarded.state() is CircuitState.CLOSED


def test_an_answer_the_caller_did_not_like_never_opens_it(store, clock):
    guarded = circuit(store, clock, failures=2)

    fail(guarded, 5, with_=refused)

    assert guarded.state() is CircuitState.CLOSED


def test_what_counts_is_the_projects_to_say(store, clock):
    guarded = circuit(
        store, clock, failures=2, counts=lambda error: isinstance(error, Refused)
    )

    fail(guarded, 2, with_=refused)

    assert guarded.state() is CircuitState.OPEN


def test_once_its_time_passes_one_call_alone_tries_and_an_answer_closes_it(store, clock):
    guarded = circuit(store, clock, failures=1, open_for=timedelta(seconds=30))
    fail(guarded, 1)
    clock.passes(31)
    assert guarded.state() is CircuitState.HALF_OPEN
    seen: list[str] = []

    def probe() -> str:
        with pytest.raises(CircuitOpen):  # the others still fail at once while it tries
            guarded.call(lambda: seen.append("second"))
        return "back"

    assert guarded.call(probe) == "back"
    assert seen == [] and guarded.state() is CircuitState.CLOSED


def test_a_probe_that_fails_opens_it_again(store, clock):
    guarded = circuit(store, clock, failures=1, open_for=timedelta(seconds=30))
    fail(guarded, 1)
    clock.passes(31)

    fail(guarded, 1)

    assert guarded.state() is CircuitState.OPEN


def test_replicas_sharing_a_store_open_and_close_together(store, clock):
    one = circuit(store, clock, failures=2)
    another = circuit(store, clock, failures=2)

    fail(one, 1)
    fail(another, 1)

    with pytest.raises(CircuitOpen):
        one.call(lambda: None)
    one.reset()
    assert another.call(lambda: "through") == "through"


def test_the_same_circuit_guards_a_block_a_function_a_method_and_a_coroutine(store, clock):
    guarded = circuit(store, clock, failures=4)

    with pytest.raises(Down):
        with guarded:
            down()

    @guarded
    def function() -> None:
        down()

    class Adapter:
        def __init__(self, circuit: CircuitBreaker) -> None:
            self.circuit = circuit

        def send(self) -> None:
            with self.circuit:
                down()

    @guarded
    async def coroutine() -> None:
        down()

    with pytest.raises(Down):
        function()
    with pytest.raises(Down):
        Adapter(guarded).send()
    with pytest.raises(Down):
        asyncio.run(coroutine())

    assert guarded.state() is CircuitState.OPEN


def test_a_circuit_built_before_any_execution_keeps_its_state_where_the_bus_keeps_its_leases():
    gateway = CircuitBreaker("bus-gateway", failures=1)  # built with the adapter, at boot
    kept = InMemoryContexts()
    shop = UseFramework("circuit-shop", log_after_execution=False)
    shop.store_context_in(kept)

    class CommandPay(DataTransferObject):
        amount: int

    @shop.feature(CommandPay)
    class Pay(Feature):
        def execute(self, dto: CommandPay) -> None:
            gateway.call(down)

    with pytest.raises(Down):
        shop(CommandPay(amount=10))
    with pytest.raises(CircuitOpen):
        shop(CommandPay(amount=10))

    assert kept.leases().store.get("sincpro:circuit_breaker:bus-gateway:open") is not None


class Unanswering(InMemoryKeyValue):
    def get_many(self, keys: list[str]) -> list[bytes | None]:
        raise StoreUnavailable("the store did not answer")


def test_a_store_that_does_not_answer_never_blocks_a_call(clock):
    guarded = circuit(Unanswering(clock.now), clock, failures=1)

    assert guarded.call(lambda: "through") == "through"
    with pytest.raises(Down):
        guarded.call(down)
    assert guarded.call(lambda: "still through") == "still through"


class NoLeases(ContextStore):
    """A project's own context store, written before leases existed."""

    def save(self, key, values, ttl=None) -> None: ...

    def load(self, key):
        return None

    def delete(self, key) -> None: ...

    def version(self, key) -> int:
        return 0


def test_a_context_store_that_keeps_no_leases_still_has_circuits_never_a_failed_call():
    gateway = CircuitBreaker("no-leases-gateway", failures=1)
    legacy = UseFramework("circuit-legacy-store", log_after_execution=False)
    legacy.store_context_in(NoLeases())

    class CommandSend(DataTransferObject):
        fail: bool

    @legacy.feature(CommandSend)
    class Send(Feature):
        def execute(self, dto: CommandSend) -> str:
            return gateway.call(down) if dto.fail else gateway.call(lambda: "sent")

    assert legacy(CommandSend(fail=False)) == "sent"
    with pytest.raises(Down):
        legacy(CommandSend(fail=True))
    with pytest.raises(CircuitOpen):  # kept in this process's memory
        legacy(CommandSend(fail=False))


def test_a_circuit_that_could_never_work_is_refused_when_built():
    with pytest.raises(exceptions.ProgrammingError, match="failures=0"):
        CircuitBreaker("never", failures=0)
    with pytest.raises(exceptions.ProgrammingError, match="open_for"):
        CircuitBreaker("never", open_for=timedelta(0))


def cancelled() -> None:
    raise asyncio.CancelledError()


def test_a_cancelled_call_neither_counts_nor_clears_the_count(store, clock):
    guarded = circuit(store, clock, failures=3)

    fail(guarded, 2)
    with pytest.raises(asyncio.CancelledError):
        guarded.call(cancelled)
    fail(guarded, 1)

    assert guarded.state() is CircuitState.OPEN


def test_a_call_cancelled_by_a_deadline_outside_it_never_clears_the_failures(store, clock):
    guarded = circuit(store, clock, failures=2)

    @guarded
    async def hung() -> None:
        await asyncio.sleep(10)

    fail(guarded, 1)
    with pytest.raises(TimeoutError):
        asyncio.run(asyncio.wait_for(hung(), 0.01))
    fail(guarded, 1)

    assert guarded.state() is CircuitState.OPEN


def test_a_cancelled_probe_leaves_it_half_open_and_lets_the_next_call_try(store, clock):
    guarded = circuit(store, clock, failures=1, open_for=timedelta(seconds=30))
    fail(guarded, 1)
    clock.passes(31)

    with pytest.raises(KeyboardInterrupt):
        with guarded:
            raise KeyboardInterrupt

    assert guarded.state() is CircuitState.HALF_OPEN
    assert guarded.call(lambda: "tried") == "tried"
    assert guarded.state() is CircuitState.CLOSED


def test_a_block_exited_in_another_context_never_masks_what_it_raised(store, clock):
    guarded = circuit(store, clock)

    contextvars.Context().run(guarded.__enter__)
    exited = contextvars.Context().run(guarded.__exit__, Down, Down("gone"), None)

    assert exited is None  # Python re-raises the block's own error, not one of the circuit


def test_blocks_exited_out_of_order_settle_each_its_own_call(store, clock):
    lenient = CircuitBreaker("lenient", failures=3, store=store, clock=clock.seconds)
    probed = CircuitBreaker("probed", failures=1, store=store, clock=clock.seconds)
    fail(probed, 1)
    clock.passes(31)

    lenient.__enter__()
    probed.__enter__()  # the probe
    lenient.__exit__(Down, Down("gone"), None)
    probed.__exit__(None, None, None)

    assert lenient.state() is CircuitState.CLOSED  # one failure of three, not a probe's
    assert probed.state() is CircuitState.CLOSED  # its probe answered


class Broken(InMemoryKeyValue):
    def increment(self, key: str) -> int:
        raise RuntimeError("a bug in the store")


def test_what_goes_wrong_keeping_the_circuit_never_replaces_what_the_call_raised(clock):
    guarded = circuit(Broken(clock.now), clock)

    with pytest.raises(Down):
        with guarded:
            down()
    with pytest.raises(Down):
        guarded.call(down)


def test_a_probe_outliving_its_time_keeps_the_others_out_for_probe_for(store, clock):
    guarded = circuit(
        store,
        clock,
        failures=1,
        open_for=timedelta(seconds=30),
        probe_for=timedelta(seconds=120),
    )
    fail(guarded, 1)
    clock.passes(31)
    seen: list[str] = []

    def slow_probe() -> str:
        clock.passes(60)  # longer than open_for
        with pytest.raises(CircuitOpen):
            guarded.call(lambda: seen.append("second"))
        return "back"

    assert guarded.call(slow_probe) == "back"
    assert seen == [] and guarded.state() is CircuitState.CLOSED


def test_a_late_answer_never_closes_a_circuit_another_probe_opened_again(store, clock):
    guarded = circuit(store, clock, failures=1, open_for=timedelta(seconds=30))
    fail(guarded, 1)
    clock.passes(31)

    late = guarded.admit()  # a probe that hangs past its time
    clock.passes(31)
    second = guarded.admit()  # its key lapsed: another probe, which fails
    guarded.settle(second, Down("still down"))
    guarded.settle(late, None)  # the first one answers, late

    assert guarded.state() is CircuitState.OPEN
    clock.passes(31)
    assert guarded.state() is CircuitState.HALF_OPEN  # never closed without a probe


def test_retry_after_is_never_below_a_second(store, clock):
    guarded = circuit(store, clock)
    lapsing = repr(clock.seconds() - 5).encode()  # a whole-second TTL outliving its time
    store.set("sincpro:circuit_breaker:gateway:open", lapsing, timedelta(seconds=10))

    with pytest.raises(CircuitOpen) as refused_call:
        guarded.call(lambda: None)

    assert refused_call.value.retry_after == timedelta(seconds=1)
    assert CircuitOpen("x", timedelta(seconds=0.2)).retry_after == timedelta(seconds=1)


def test_a_generator_is_guarded_while_it_is_iterated(store, clock):
    guarded = circuit(store, clock, failures=1)

    @guarded
    def rows():
        yield 1
        down()

    assert next(rows()) == 1  # abandoned: neither an answer nor a failure
    assert guarded.state() is CircuitState.CLOSED
    with pytest.raises(Down):
        list(rows())

    assert guarded.state() is CircuitState.OPEN


def test_an_async_generator_is_guarded_while_it_is_iterated(store, clock):
    guarded = circuit(store, clock, failures=1)

    @guarded
    async def rows():
        received = yield 1
        yield received
        down()

    async def iterate() -> list:
        got = []
        stream = rows()
        got.append(await stream.__anext__())
        got.append(await stream.asend("sent"))
        with pytest.raises(Down):
            await stream.__anext__()
        return got

    assert asyncio.run(iterate()) == [1, "sent"]
    assert guarded.state() is CircuitState.OPEN
