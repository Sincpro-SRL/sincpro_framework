"""`Cache` when something fails: the source (fail-safe), the shared store (the breaker), and what
each call reports so neither hides an outage.

The source stands in for another system a service keeps answers of — a tenant registry — that
goes down, answers slowly, or revokes what it granted.
"""

import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from sincpro_framework import DataTransferObject
from sincpro_framework.caching import (
    Cache,
    CacheOutcome,
    CountingObserver,
    ExternalVersion,
    FailSafe,
    InMemoryKeyValue,
    JsonCodec,
    KeepPolicy,
    KeyValueStore,
    TimeToLive,
)
from sincpro_framework.testing import ManualClock


class Tenant(DataTransferObject):
    name: str
    version: str


@dataclass
class Source:
    """The other system: `down` makes every call raise, as a registry that stopped answering."""

    version: str = "v1"
    down: bool = False
    calls: int = 0
    version_down: bool = False

    def resolve(self) -> Tenant:
        self.calls += 1
        if self.down:
            raise ConnectionError("the registry is down")
        return Tenant(name=f"tenant-{self.calls}", version=self.version)

    def current_version(self) -> str:
        if self.version_down:
            raise TimeoutError("the version endpoint timed out")
        return self.version


class FlakyStore(KeyValueStore):
    """A shared store that can stop answering — every operation, or only writes."""

    def __init__(self, clock: ManualClock) -> None:
        self.inner = InMemoryKeyValue(now=clock.now)
        self.down = False
        self.writes_down = False
        self.operations = 0

    def _operated(self, writing: bool = False) -> None:
        self.operations += 1
        if self.down or (writing and self.writes_down):
            raise ConnectionError("the store is not answering")

    def get_many(self, keys: list[str]) -> list[bytes | None]:
        self._operated()
        return self.inner.get_many(keys)

    def set(self, key: str, value: bytes, ttl: timedelta | None = None) -> None:
        self._operated(writing=True)
        self.inner.set(key, value, ttl)

    def add(self, key: str, value: bytes, ttl: timedelta | None = None) -> bool:
        self._operated()
        return self.inner.add(key, value, ttl)

    def increment(self, key: str) -> int:
        self._operated(writing=True)
        return self.inner.increment(key)

    def delete(self, key: str) -> None:
        self._operated()
        self.inner.delete(key)

    def take(self, key: str) -> bytes | None:
        self._operated()
        return self.inner.take(key)


@dataclass
class World:
    cache: Cache
    clock: ManualClock
    codec: JsonCodec[Tenant] | None
    observer: CountingObserver
    source: Source = field(default_factory=Source)
    store: FlakyStore | None = None

    def ask(self, policy: KeepPolicy[Tenant] | None = None, key: str = "tok") -> Tenant:
        return self.cache.get_or_compute(
            ("tenant", key), self.source.resolve, policy, self.codec
        )

    def count(self, outcome: CacheOutcome) -> int:
        return self.observer.of(outcome, "tenants")


def _world(tier: str) -> World:
    clock = ManualClock(datetime(2026, 9, 29, 12, 0, tzinfo=UTC))
    observer = CountingObserver()
    if tier == "process":
        cache = Cache(
            namespace="tenants", now=clock.now, random=lambda: 0.5, observer=observer
        )
        return World(cache, clock, None, observer)
    store = FlakyStore(clock)
    cache = Cache(
        store, namespace="tenants", now=clock.now, random=lambda: 0.5, observer=observer
    )
    return World(cache, clock, JsonCodec(Tenant), observer, store=store)


TIERS = ["process", "store"]
FAIL_SAFE = KeepPolicy[Tenant](
    freshness=TimeToLive(ttl=timedelta(minutes=5)),
    failure=FailSafe(serve_for=timedelta(hours=1), throttle_for=timedelta(seconds=30)),
)


# Fail-safe


@pytest.mark.parametrize("tier", TIERS)
def test_a_source_that_is_down_is_answered_with_the_last_good_value_asked_once_per_throttle(
    tier,
):
    """The incident fail-safe is for: the registry goes down and every call that needs a tenant
    fails the moment its value lapses — though it was right a minute ago. And a source that is
    down must not be hammered by every call while it recovers."""
    world = _world(tier)
    first = world.ask(FAIL_SAFE)
    world.clock.advance(minutes=6)
    world.source.down = True

    served = world.ask(FAIL_SAFE)
    for _ in range(5):
        world.clock.advance(seconds=5)
        world.ask(FAIL_SAFE)
    assert world.source.calls == 2  # once to compute, once to find it down — then throttled

    world.clock.advance(seconds=10)
    world.ask(FAIL_SAFE)
    assert world.source.calls == 3  # past throttle_for the source is asked again

    world.source.down = False
    world.clock.advance(seconds=31)
    recovered = world.ask(FAIL_SAFE)

    assert served == first
    assert recovered.name == "tenant-4"
    assert world.count(CacheOutcome.FALLBACK) == 2


@pytest.mark.parametrize("tier", TIERS)
def test_past_its_last_resort_a_failing_source_is_the_error(tier):
    world = _world(tier)
    world.ask(FAIL_SAFE)
    world.clock.advance(minutes=5, hours=1, seconds=1)
    world.source.down = True

    with pytest.raises(ConnectionError):
        world.ask(FAIL_SAFE)


@pytest.mark.parametrize("tier", TIERS)
def test_a_computation_that_fails_after_the_last_resort_passed_is_the_error(tier):
    """A source that hangs until its timeout: the value was inside its last resort when the call
    started, and is not by the time computing fails — it is not served."""
    world = _world(tier)
    world.ask(FAIL_SAFE)
    world.clock.advance(minutes=4, seconds=59, hours=1)  # a second inside its last resort

    def times_out() -> Tenant:
        world.clock.advance(seconds=2)
        raise TimeoutError("the registry timed out")

    with pytest.raises(TimeoutError):
        world.cache.get_or_compute(("tenant", "tok"), times_out, FAIL_SAFE, world.codec)


@pytest.mark.parametrize("tier", TIERS)
def test_without_a_failure_policy_nothing_is_served_past_its_servable_life(tier):
    """Serving stale data is a choice, never a surprise: the default raises."""
    world = _world(tier)
    policy = KeepPolicy[Tenant](freshness=TimeToLive(ttl=timedelta(minutes=5)))
    world.ask(policy)
    world.clock.advance(minutes=6)
    world.source.down = True

    with pytest.raises(ConnectionError):
        world.ask(policy)


@pytest.mark.parametrize("tier", TIERS)
def test_a_value_its_validation_rejected_is_never_served_as_a_fallback(tier):
    """Fail-safe covers a source that cannot answer, not one that answered no: a tenant whose
    access the registry revoked is not handed out because recomputing it then failed."""
    world = _world(tier)
    policy = KeepPolicy[Tenant](
        validation=ExternalVersion(
            world.source.current_version, kept=lambda one: one.version
        ),
        failure=FailSafe(serve_for=timedelta(hours=1)),
    )
    world.ask(policy)
    world.source.version = "v2"
    world.source.down = True

    with pytest.raises(ConnectionError):
        world.ask(policy)
    assert world.count(CacheOutcome.FALLBACK) == 0


@pytest.mark.parametrize("tier", TIERS)
def test_a_validation_that_cannot_tell_falls_back_and_spares_the_version_endpoint(tier):
    """A version endpoint timing out is "cannot tell", not "wrong": the policy decides."""
    world = _world(tier)
    policy = KeepPolicy[Tenant](
        validation=ExternalVersion(
            world.source.current_version, kept=lambda one: one.version
        ),
        failure=FailSafe(serve_for=timedelta(hours=1), throttle_for=timedelta(seconds=30)),
    )
    first = world.ask(policy)
    world.source.version_down = True

    served = world.ask(policy)
    again = world.ask(policy)

    assert served == again == first
    assert world.source.calls == 1
    assert world.count(CacheOutcome.FALLBACK) == 1  # the second call was a trusted hit
    assert world.count(CacheOutcome.HIT) == 1


def test_a_validation_that_cannot_tell_is_the_error_without_a_failure_policy():
    world = _world("process")
    policy = KeepPolicy[Tenant](
        validation=ExternalVersion(world.source.current_version, kept=lambda one: one.version)
    )
    world.ask(policy)
    world.source.version_down = True

    with pytest.raises(TimeoutError):
        world.ask(policy)


def test_an_error_the_policy_does_not_list_is_raised():
    """A timeout is not a 401: `errors` names the failures the last good value stands in for."""
    world = _world("process")
    policy = KeepPolicy[Tenant](
        freshness=TimeToLive(ttl=timedelta(minutes=5)),
        failure=FailSafe(serve_for=timedelta(hours=1), errors=(TimeoutError,)),
    )
    world.ask(policy)
    world.clock.advance(minutes=6)
    world.source.down = True  # raises ConnectionError, not listed

    with pytest.raises(ConnectionError):
        world.ask(policy)


def test_a_base_exception_is_never_answered_with_a_fallback():
    class Cancelled(BaseException):
        pass

    world = _world("process")
    world.ask(FAIL_SAFE)
    world.clock.advance(minutes=6)

    def cancelled() -> Tenant:
        raise Cancelled()

    with pytest.raises(Cancelled):
        world.cache.get_or_compute(("tenant", "tok"), cancelled, FAIL_SAFE)


def test_the_shared_store_keeps_a_value_until_its_last_resort():
    """A value the store expired at its stale end could not be a fallback on another replica."""
    world = _world("store")
    world.ask(FAIL_SAFE)
    assert world.store is not None
    world.clock.advance(minutes=50)

    replica = Cache(world.store, namespace="tenants", now=world.clock.now)
    world.source.down = True
    served = replica.get_or_compute(
        ("tenant", "tok"), world.source.resolve, FAIL_SAFE, JsonCodec(Tenant)
    )

    assert served.name == "tenant-1"


# The store breaker


def test_a_store_that_fails_is_bypassed_and_the_call_is_answered_in_the_process():
    """A Redis that stops answering must not turn every cached call into an error."""
    world = _world("store")
    assert world.store is not None
    world.store.down = True

    first = world.ask()
    operations = world.store.operations
    again = world.ask()

    assert first == again
    assert world.source.calls == 1
    assert world.store.operations == operations  # while open, the store is not asked
    assert world.count(CacheOutcome.BYPASSED) == 2
    assert world.count(CacheOutcome.COMPUTED) == 1
    assert world.count(CacheOutcome.HIT) == 1


def test_the_first_call_after_bypass_for_tries_the_store_again():
    world = _world("store")
    assert world.store is not None
    world.store.down = True
    world.ask()

    world.store.down = False
    world.clock.advance(seconds=31)
    world.ask()
    replica = Cache(world.store, namespace="tenants", now=world.clock.now)
    kept = replica.get_or_compute(
        ("tenant", "tok"), world.source.resolve, None, JsonCodec(Tenant)
    )

    assert kept.name == "tenant-2"  # computed on the store after the window, and kept there
    assert world.source.calls == 2


def test_a_failure_to_write_never_fails_the_call_and_trips_the_breaker():
    world = _world("store")
    assert world.store is not None
    world.store.writes_down = True

    answered = world.ask()
    operations = world.store.operations
    world.ask()

    assert answered.name == "tenant-1"
    assert world.store.operations == operations  # the next call bypassed the store
    assert world.count(CacheOutcome.BYPASSED) == 1


def test_callers_during_a_bypass_still_compute_once():
    """A store outage must not also become a stampede on the source."""
    world = _world("store")
    assert world.store is not None
    world.store.down = True
    world.ask(key="warm")  # trips the breaker
    computed: list[int] = []

    def slow() -> Tenant:
        computed.append(1)
        time.sleep(0.05)
        return Tenant(name="slow", version="v1")

    threads = [
        threading.Thread(
            target=lambda: world.cache.get_or_compute(
                ("tenant", "hot"), slow, None, world.codec
            )
        )
        for _ in range(10)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(computed) == 1


def test_forgetting_while_the_store_fails_is_an_error_not_a_silent_no_op():
    """A forget that reached no replica must be known to whoever asked for it."""
    world = _world("store")
    assert world.store is not None
    world.ask()
    world.store.down = True

    with pytest.raises(ConnectionError):
        world.cache.forget(("tenant", "tok"), world.codec)


# What each call reports


@pytest.mark.parametrize("tier", TIERS)
def test_every_call_reports_exactly_one_final_outcome(tier):
    world = _world(tier)
    policy = KeepPolicy[Tenant](
        freshness=TimeToLive(ttl=timedelta(minutes=5), stale_for=timedelta(minutes=5)),
        validation=ExternalVersion(
            world.source.current_version, kept=lambda one: one.version
        ),
        failure=FailSafe(serve_for=timedelta(hours=1)),
        wait_for_others=timedelta(milliseconds=20),
    )
    world.ask(policy)  # COMPUTED
    world.ask(policy)  # HIT
    world.clock.advance(minutes=6)
    hashed = world.cache._key(("tenant", "tok"), world.codec)
    tier_of = world.cache._tier(world.codec)
    assert tier_of.lead(hashed, policy)
    world.ask(policy)  # STALE: another caller leads
    tier_of.release(hashed)
    world.source.version = "v2"
    world.ask(policy)  # INVALIDATED
    world.clock.advance(minutes=11)
    world.source.down = True
    world.ask(policy)  # FALLBACK

    finals = [
        CacheOutcome.HIT,
        CacheOutcome.STALE,
        CacheOutcome.COMPUTED,
        CacheOutcome.COALESCED,
        CacheOutcome.INVALIDATED,
        CacheOutcome.FALLBACK,
    ]
    assert {one: world.count(one) for one in finals} == {
        CacheOutcome.HIT: 1,
        CacheOutcome.STALE: 1,
        CacheOutcome.COMPUTED: 1,
        CacheOutcome.COALESCED: 0,
        CacheOutcome.INVALIDATED: 1,
        CacheOutcome.FALLBACK: 1,
    }


def test_a_caller_served_the_leaders_value_reports_coalesced():
    world = _world("process")
    started = threading.Event()

    def slow() -> Tenant:
        started.set()
        time.sleep(0.05)
        return Tenant(name="slow", version="v1")

    leader = threading.Thread(
        target=lambda: world.cache.get_or_compute(("tenant", "tok"), slow, None, None)
    )
    leader.start()
    started.wait()
    served = world.ask()
    leader.join()

    assert served.name == "slow"
    assert world.count(CacheOutcome.COALESCED) == 1


def test_an_observer_that_raises_never_breaks_the_call():
    class Broken:
        def observed(self, outcome: str, namespace: str) -> None:
            raise RuntimeError("the metrics exporter is down")

    cache = Cache(observer=Broken())

    assert cache.get_or_compute("k", lambda: 1) == 1
    assert cache.get_or_compute("k", lambda: 2) == 1
