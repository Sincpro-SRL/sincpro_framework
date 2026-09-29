"""`Cache`: a value kept by its parameters — not a Query, not the ORM — judged by the strategies
of each call, on the process or on a shared store.

The value stands in for what a real service keeps from another system: a tenant a token resolves
to, whose only signal of change is the version its registry publishes.
"""

import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import fakeredis
import pytest

from sincpro_framework import DataTransferObject
from sincpro_framework.caching import (
    Cache,
    ExternalVersion,
    InMemoryKeyValue,
    JsonCodec,
    KeepPolicy,
    Lifetime,
)
from sincpro_framework.caching.adapters.redis import RedisKeyValue
from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.testing import ManualClock


class Tenant(DataTransferObject):
    name: str
    version: str


@dataclass
class Registry:
    """The other system: resolving is the expensive call, the version is the cheap one."""

    version: str = "v1"
    resolved: list[str] = field(default_factory=list)
    asked_version: int = 0

    def resolve(self, token: str) -> Tenant:
        self.resolved.append(token)
        return Tenant(name=f"tenant-of-{token}", version=self.version)

    def current_version(self) -> str:
        self.asked_version += 1
        return self.version


@dataclass
class World:
    cache: Cache
    clock: ManualClock
    codec: JsonCodec[Tenant] | None
    registry: Registry = field(default_factory=Registry)


TIERS = ["process", "memory", "redis"]


def _world(tier: str) -> World:
    clock = ManualClock(datetime(2026, 9, 29, 12, 0, tzinfo=UTC))
    if tier == "process":
        return World(Cache(now=clock.now, random=lambda: 0.5), clock, None)
    store = (
        InMemoryKeyValue(now=clock.now)
        if tier == "memory"
        else RedisKeyValue(fakeredis.FakeRedis(), prefix="test:")
    )
    return World(Cache(store, now=clock.now, random=lambda: 0.5), clock, JsonCodec(Tenant))


def _tenant(world: World, token: str, policy: KeepPolicy[Tenant] | None = None) -> Tenant:
    return world.cache.get_or_compute(
        ("tenant", token), lambda: world.registry.resolve(token), policy, world.codec
    )


@pytest.mark.parametrize("tier", TIERS)
def test_asking_again_with_the_same_parameters_does_not_compute_again(tier):
    world = _world(tier)

    first, again = _tenant(world, "tok-a"), _tenant(world, "tok-a")
    other = _tenant(world, "tok-b")

    assert first == again
    assert other.name == "tenant-of-tok-b"
    assert world.registry.resolved == ["tok-a", "tok-b"]


@pytest.mark.parametrize("tier", ["process", "memory"])
def test_a_value_is_recomputed_once_its_ttl_passes(tier):
    world = _world(tier)
    policy = KeepPolicy[Tenant](lifetime=Lifetime(ttl=timedelta(minutes=5)))

    _tenant(world, "tok", policy)
    world.clock.advance(minutes=4)
    _tenant(world, "tok", policy)
    world.clock.advance(minutes=2)
    _tenant(world, "tok", policy)

    assert world.registry.resolved == ["tok", "tok"]


@pytest.mark.parametrize("tier", ["process", "memory"])
def test_past_its_ttl_the_stale_value_is_served_while_another_caller_recomputes(tier):
    """RFC 5861's stale-while-revalidate: nobody waits on the source for what was right a moment
    ago — the leader recomputes, the rest are answered at once."""
    world = _world(tier)
    policy = KeepPolicy[Tenant](
        lifetime=Lifetime(ttl=timedelta(minutes=5), stale_for=timedelta(minutes=1))
    )
    _tenant(world, "tok", policy)
    world.clock.advance(minutes=5, seconds=30)
    hashed = world.cache._key(("tenant", "tok"), world.codec)
    tier_of = world.cache._tier(world.codec)
    assert tier_of.lead(hashed, policy)  # another caller is recomputing

    served = _tenant(world, "tok", policy)

    assert served.name == "tenant-of-tok"
    assert world.registry.resolved == ["tok"]


@pytest.mark.parametrize("tier", TIERS)
def test_many_callers_missing_at_once_compute_once(tier):
    """A cold key under load: without a single leader every caller hits the source at once."""
    world = _world(tier)
    slow_calls: list[str] = []

    def slow() -> Tenant:
        slow_calls.append("x")
        time.sleep(0.05)
        return Tenant(name="slow", version="v1")

    answers: list[Tenant] = []
    lock = threading.Lock()

    def ask() -> None:
        answer = world.cache.get_or_compute(("tenant", "hot"), slow, codec=world.codec)
        with lock:
            answers.append(answer)

    threads = [threading.Thread(target=ask) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(slow_calls) == 1
    assert {answer.name for answer in answers} == {"slow"}


@pytest.mark.parametrize("tier", TIERS)
def test_a_version_that_moved_makes_the_kept_value_recomputed(tier):
    """The incident this strategy is for: access granted at the source never shows, because the
    cached value was computed before it and its lifetime alone keeps it."""
    world = _world(tier)
    policy = KeepPolicy[Tenant](
        validation=ExternalVersion(
            world.registry.current_version, kept=lambda one: one.version
        )
    )
    _tenant(world, "tok", policy)

    world.registry.version = "v2"
    refreshed = _tenant(world, "tok", policy)

    assert refreshed.version == "v2"
    assert world.registry.resolved == ["tok", "tok"]


@pytest.mark.parametrize("tier", ["process", "memory"])
def test_the_version_is_not_asked_again_while_the_last_check_is_trusted(tier):
    """A hot key must not put the source's version endpoint in the path of every call."""
    world = _world(tier)
    policy = KeepPolicy[Tenant](
        validation=ExternalVersion(
            world.registry.current_version,
            kept=lambda one: one.version,
            trusted_for=timedelta(minutes=5),
        )
    )
    _tenant(world, "tok", policy)
    for _ in range(10):
        _tenant(world, "tok", policy)
    assert world.registry.asked_version == 0

    world.clock.advance(minutes=6)
    world.registry.version = "v2"
    assert _tenant(world, "tok", policy).version == "v2"
    assert world.registry.asked_version == 1


def test_reading_the_version_off_the_value_spares_asking_before_computing():
    world = _world("process")
    kept = KeepPolicy[Tenant](
        validation=ExternalVersion(
            world.registry.current_version, kept=lambda one: one.version
        )
    )
    asked_first = KeepPolicy[Tenant](
        validation=ExternalVersion(world.registry.current_version)
    )

    _tenant(world, "tok-a", kept)
    assert world.registry.asked_version == 0
    _tenant(world, "tok-b", asked_first)
    assert world.registry.asked_version == 1


@pytest.mark.parametrize("tier", ["process", "memory"])
def test_a_value_that_no_longer_holds_is_never_served_even_when_another_caller_leads(tier):
    """The stale window covers lifetime, never validity: a value the source says is wrong is not
    handed out because someone else happens to be recomputing it."""
    world = _world(tier)
    policy = KeepPolicy[Tenant](
        lifetime=Lifetime(ttl=timedelta(minutes=5), stale_for=timedelta(minutes=5)),
        validation=ExternalVersion(
            world.registry.current_version, kept=lambda one: one.version
        ),
        wait_for_others=timedelta(milliseconds=20),
    )
    _tenant(world, "tok", policy)
    world.registry.version = "v2"
    hashed = world.cache._key(("tenant", "tok"), world.codec)
    assert world.cache._tier(world.codec).lead(hashed, policy)  # a leader that never finishes

    served = _tenant(world, "tok", policy)

    assert served.version == "v2"


@pytest.mark.parametrize("tier", TIERS)
def test_an_exception_is_never_kept_and_does_not_hold_the_key(tier):
    world = _world(tier)
    calls: list[int] = []

    def failing() -> Tenant:
        calls.append(1)
        raise RuntimeError("the registry is down")

    for _ in range(2):
        with pytest.raises(RuntimeError):
            world.cache.get_or_compute(("tenant", "tok"), failing, codec=world.codec)
    started = time.monotonic()
    assert _tenant(world, "tok").name == "tenant-of-tok"

    assert len(calls) == 2
    assert time.monotonic() - started < 0.5  # nobody waited on a leader that already failed


@pytest.mark.parametrize("tier", ["memory", "redis"])
def test_a_key_never_carries_its_parameters_in_clear(tier):
    """A token used as a key is a credential in `KEYS *` if it is not hashed."""
    world = _world(tier)
    _tenant(world, "secret-token-123")

    store = world.cache.store
    names = (
        list(store._values)  # type: ignore[union-attr]
        if isinstance(store, InMemoryKeyValue)
        else [one.decode() for one in store.client.keys("*")]  # type: ignore[union-attr]
    )

    assert names
    assert not any("secret-token-123" in name for name in names)


def test_a_shared_store_without_a_codec_is_refused():
    cache = Cache(InMemoryKeyValue())

    with pytest.raises(ContractViolation, match="Codec"):
        cache.get_or_compute("k", lambda: 1)


def test_the_process_tier_keeps_the_object_itself():
    """What a value carrying a credential needs: nothing encoded, nothing leaves the process."""
    cache = Cache()
    value = {"api_key": "KEY"}

    assert cache.get_or_compute("k", lambda: value) is value
    assert cache.get_or_compute("k", lambda: {"api_key": "other"}) is value


@pytest.mark.parametrize("tier", TIERS)
def test_forgetting_a_key_lets_go_of_its_value(tier):
    world = _world(tier)
    _tenant(world, "tok")

    world.cache.forget(("tenant", "tok"), world.codec)
    _tenant(world, "tok")

    assert world.registry.resolved == ["tok", "tok"]


def test_switched_off_every_call_computes_and_nothing_is_kept():
    world = _world("memory")
    world.cache.enabled = False

    _tenant(world, "tok")
    _tenant(world, "tok")

    assert world.registry.resolved == ["tok", "tok"]


def test_a_value_larger_than_max_bytes_is_answered_and_not_kept():
    world = _world("memory")
    policy = KeepPolicy[Tenant](max_bytes=10)

    _tenant(world, "tok", policy)
    _tenant(world, "tok", policy)

    assert world.registry.resolved == ["tok", "tok"]


def test_a_deploy_that_changes_the_shape_reads_its_own_key_not_the_old_value():
    """A value kept by an older deploy, in a shape this one does not read, never reaches the
    caller half-right: the new shape is another key."""
    store = InMemoryKeyValue()

    class TenantV2(DataTransferObject):
        name: str
        version: str
        plan: str

    Cache(store).get_or_compute(
        "t", lambda: Tenant(name="a", version="v1"), codec=JsonCodec(Tenant)
    )
    upgraded = Cache(store).get_or_compute(
        "t", lambda: TenantV2(name="a", version="v1", plan="pro"), codec=JsonCodec(TenantV2)
    )

    assert upgraded.plan == "pro"


def test_a_value_that_cannot_be_read_is_a_miss_not_an_error():
    store = InMemoryKeyValue()
    cache = Cache(store)
    codec = JsonCodec(Tenant)
    cache.get_or_compute("t", lambda: Tenant(name="a", version="v1"), codec=codec)
    store.set(cache._key("t", codec), b'{"mark": ""}\n not json')

    again = cache.get_or_compute("t", lambda: Tenant(name="b", version="v1"), codec=codec)

    assert again.name == "b"


@pytest.mark.parametrize("tier", ["process", "memory"])
def test_past_its_ttl_inside_the_stale_window_the_leader_recomputes_it(tier):
    """Stale-while-revalidate needs a leader: with nobody else recomputing, the caller that finds
    the value expiring computes the new one — or the stale value would be served forever."""
    world = _world(tier)
    policy = KeepPolicy[Tenant](
        lifetime=Lifetime(ttl=timedelta(minutes=5), stale_for=timedelta(minutes=5))
    )
    _tenant(world, "tok", policy)
    world.registry.version = "v2"
    world.clock.advance(minutes=6)

    refreshed = _tenant(world, "tok", policy)

    assert refreshed.version == "v2"
    assert world.registry.resolved == ["tok", "tok"]


@pytest.mark.parametrize("tier", ["process", "memory"])
def test_a_check_that_held_is_trusted_again_for_another_window(tier):
    """The version is asked once per `trusted_for`, not once ever and not on every call."""
    world = _world(tier)
    policy = KeepPolicy[Tenant](
        validation=ExternalVersion(
            world.registry.current_version,
            kept=lambda one: one.version,
            trusted_for=timedelta(minutes=5),
        )
    )
    _tenant(world, "tok", policy)

    world.clock.advance(minutes=6)
    _tenant(world, "tok", policy)
    for _ in range(5):
        _tenant(world, "tok", policy)
    assert world.registry.asked_version == 1

    world.clock.advance(minutes=6)
    _tenant(world, "tok", policy)
    assert world.registry.asked_version == 2
    assert world.registry.resolved == ["tok"]


@pytest.mark.parametrize("tier", ["process", "memory"])
def test_a_caller_waiting_on_a_leader_that_never_finishes_computes_itself(tier):
    """A waiter never hangs: past `wait_for_others` it computes on its own."""
    world = _world(tier)
    policy = KeepPolicy[Tenant](wait_for_others=timedelta(milliseconds=30))
    hashed = world.cache._key(("tenant", "tok"), world.codec)
    assert world.cache._tier(world.codec).lead(hashed, policy)  # a leader that never finishes

    started = time.monotonic()
    answer = _tenant(world, "tok", policy)

    assert answer.name == "tenant-of-tok"
    assert time.monotonic() - started < 1.0


def test_a_validation_that_settles_no_mark_answers_and_keeps_nothing():
    """`after()` answering `None` is how a validation says "this one is not to be kept"."""
    from sincpro_framework.caching import Validation

    class NeverKept(Validation[Tenant]):
        def after(self, value: Tenant, before: str | None) -> str | None:
            return None

        def holds(self, mark: str) -> bool:
            return True

    world = _world("process")
    policy = KeepPolicy[Tenant](validation=NeverKept())

    _tenant(world, "tok", policy)
    _tenant(world, "tok", policy)

    assert world.registry.resolved == ["tok", "tok"]
