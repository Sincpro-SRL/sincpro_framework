"""A lease holds a name for one owner at a time, across threads and replicas: taken atomically,
renewed and released only by its holder, `EXPIRED` — not free — when its holder stopped renewing,
and fenced so a holder that wakes up late is told apart from the one that holds it now."""

import threading
import time
from datetime import UTC, datetime, timedelta

import fakeredis
import pytest

from sincpro_framework.exceptions import FailureKind
from sincpro_framework.store import (
    InMemoryKeyValue,
    KeyValueStore,
    Leases,
    LeaseState,
)
from sincpro_framework.store.adapters.redis import RedisKeyValue
from sincpro_framework.store.domain import exceptions as store_exceptions


class Clock:
    def __init__(self) -> None:
        self.at = datetime(2026, 10, 23, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.at

    def passes(self, seconds: float) -> None:
        self.at += timedelta(seconds=seconds)


def leases_on_a_clock() -> tuple[Leases, Clock]:
    clock = Clock()
    return Leases(InMemoryKeyValue(now=clock)), clock


def test_one_owner_holds_a_name_at_a_time():
    leases, _ = leases_on_a_clock()

    first = leases.take("sandbox:S-1", owner="worker-a")
    second = leases.take("sandbox:S-1", owner="worker-b")

    assert first is not None and second is None
    assert (leases.state("sandbox:S-1"), leases.holder("sandbox:S-1")) == (
        LeaseState.HELD,
        "worker-a",
    )


def test_a_released_name_is_free_and_the_next_taker_gets_a_higher_fence():
    leases, _ = leases_on_a_clock()
    first = leases.take("sandbox:S-1", owner="worker-a")
    assert first is not None

    first.release()
    second = leases.take("sandbox:S-1", owner="worker-b")

    assert second is not None and (first.fence, second.fence) == (1, 2)
    second.release()
    assert leases.state("sandbox:S-1") is LeaseState.FREE


def test_a_holder_that_stopped_renewing_leaves_the_name_expired_not_free():
    leases, clock = leases_on_a_clock()
    leases.take("sandbox:S-1", owner="worker-a", ttl=timedelta(seconds=30))

    clock.passes(31)

    assert leases.state("sandbox:S-1") is LeaseState.EXPIRED
    assert leases.holder("sandbox:S-1") == "worker-a"  # who to look for
    taken = leases.take("sandbox:S-1", owner="worker-b")
    assert (
        taken is not None
        and taken.fence == 2
        and leases.state("sandbox:S-1") is LeaseState.HELD
    )


def test_a_holder_that_wakes_up_late_can_neither_renew_nor_free_the_new_holders_lease():
    leases, clock = leases_on_a_clock()
    late = leases.take("sandbox:S-1", owner="worker-a", ttl=timedelta(seconds=30))
    assert late is not None
    clock.passes(31)
    now = leases.take("sandbox:S-1", owner="worker-b", ttl=timedelta(seconds=30))
    assert now is not None

    with pytest.raises(store_exceptions.LeaseLost) as lost:
        late.renew()
    late.release()

    assert lost.value.failure_kind is FailureKind.CONFLICT
    assert late.lost.is_set() and late.fence < now.fence
    assert leases.holder("sandbox:S-1") == "worker-b"


def test_of_many_racing_to_take_a_name_one_holds_it():
    leases = Leases(InMemoryKeyValue())
    won: list[str] = []
    lock = threading.Lock()

    def race(number: int) -> None:
        if leases.take("sandbox:S-1", owner=f"worker-{number}") is not None:
            with lock:
                won.append(f"worker-{number}")

    threads = [threading.Thread(target=race, args=(one,)) for one in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(won) == 1 and leases.holder("sandbox:S-1") == won[0]


def test_a_held_lease_outlives_its_own_time_while_the_block_runs_and_is_released_after():
    leases = Leases(InMemoryKeyValue())

    with leases.held("run:R-7", owner="worker-a", ttl=timedelta(seconds=0.3)) as lease:
        time.sleep(0.8)  # more than twice its time: renewed in the background
        lease.check()
        assert leases.state("run:R-7") is LeaseState.HELD

    assert leases.state("run:R-7") is LeaseState.FREE


def test_a_second_holder_is_refused_with_who_holds_it():
    leases = Leases(InMemoryKeyValue())

    with leases.held("run:R-7", owner="worker-a"):
        with pytest.raises(store_exceptions.LeaseTaken) as taken:
            with leases.held("run:R-7", owner="worker-b"):
                pass

    assert taken.value.holder == "worker-a"
    assert taken.value.failure_kind is FailureKind.IN_PROGRESS


def test_a_block_learns_it_lost_its_lease_at_its_next_check():
    store = InMemoryKeyValue()
    leases = Leases(store)

    with leases.held(
        "run:R-7",
        owner="worker-a",
        ttl=timedelta(seconds=5),
        renew_every=timedelta(seconds=0.05),
    ) as lease:
        store.delete(leases.claim_key("run:R-7"))  # its time passed, as if it paused
        assert leases.take("run:R-7", owner="worker-b") is not None
        time.sleep(0.2)
        with pytest.raises(store_exceptions.LeaseLost):
            lease.check()

    assert leases.holder("run:R-7") == "worker-b"  # its end did not free the new holder's


def test_it_holds_on_redis_as_in_memory():
    leases = Leases(RedisKeyValue(fakeredis.FakeRedis(), prefix="test:"))
    first = leases.take("sandbox:S-1", owner="worker-a")
    assert first is not None and leases.take("sandbox:S-1", owner="worker-b") is None

    first.renew()
    first.release()

    assert leases.state("sandbox:S-1") is LeaseState.FREE


class WithoutReplace(KeyValueStore):
    """A store of a project's that cannot compare before it writes."""

    def __init__(self) -> None:
        self.inner = InMemoryKeyValue()

    def get_many(self, keys: list[str]) -> list[bytes | None]:
        return self.inner.get_many(keys)

    def set(self, key: str, value: bytes, ttl: timedelta | None = None) -> None:
        self.inner.set(key, value, ttl)

    def add(self, key: str, value: bytes, ttl: timedelta | None = None) -> bool:
        return self.inner.add(key, value, ttl)

    def increment(self, key: str) -> int:
        return self.inner.increment(key)

    def delete(self, key: str) -> None:
        self.inner.delete(key)

    def take(self, key: str) -> bytes | None:
        return self.inner.take(key)


def test_a_store_that_cannot_compare_is_a_store_until_a_lease_renews_on_it():
    store = WithoutReplace()
    store.set("a", b"1")
    lease = Leases(store).take("sandbox:S-1", owner="worker-a")
    assert store.get("a") == b"1" and lease is not None

    with pytest.raises(
        store_exceptions.ProgrammingError, match="cannot compare before it writes"
    ):
        lease.renew()
