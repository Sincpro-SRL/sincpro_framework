"""The strategies a `Cache` is built from — `Freshness`, `Eviction`, `Codec` — each held to its
contract suite, and the incidents each one is there for.
"""

import threading
from datetime import UTC, datetime, timedelta

import pytest

from sincpro_framework import DataTransferObject
from sincpro_framework.caching import (
    Cache,
    InMemoryKeyValue,
    JsonCodec,
    KeepPolicy,
    Lifetime,
    Lru,
    Sliding,
    TimeToLive,
    Unbounded,
)
from sincpro_framework.caching.domain.codec import Codec
from sincpro_framework.caching.domain.eviction import Eviction
from sincpro_framework.caching.domain.freshness import Freshness
from sincpro_framework.caching.testing import (
    CodecContract,
    EvictionContract,
    FreshnessContract,
)
from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.testing import ManualClock


class Tenant(DataTransferObject):
    name: str
    version: str


# Every built-in passes the suite a project's own does


class TestTimeToLive(FreshnessContract):
    def make_freshness(self) -> Freshness:
        return TimeToLive(
            ttl=timedelta(minutes=5),
            jitter=0.2,
            stale_for=timedelta(minutes=1),
            early_expiry=1.0,
        )


class TestTimeToLiveThatNeverExpires(FreshnessContract):
    def make_freshness(self) -> Freshness:
        return TimeToLive()


class TestSliding(FreshnessContract):
    def make_freshness(self) -> Freshness:
        return Sliding(idle_for=timedelta(minutes=10), at_most=timedelta(hours=1))


class TestLru(EvictionContract):
    bound = 5

    def make_eviction(self) -> Eviction:
        return Lru(max_entries=5)


class TestUnbounded(EvictionContract):
    bound = None

    def make_eviction(self) -> Eviction:
        return Unbounded()


class TestJsonCodecOfADto(CodecContract):
    def make_codec(self) -> Codec[Tenant]:
        return JsonCodec(Tenant)

    def samples(self) -> list[Tenant]:
        return [Tenant(name="acme", version="v1"), Tenant(name="", version="ü")]

    def make_other_codec(self) -> Codec[object] | None:
        class TenantV2(DataTransferObject):
            name: str
            version: str
            plan: str

        return JsonCodec(TenantV2)


class TestJsonCodecOfAMapping(CodecContract):
    def make_codec(self) -> Codec[dict[str, int]]:
        return JsonCodec(dict[str, int])

    def samples(self) -> list[dict[str, int]]:
        return [{}, {"a": 1, "b": -2}]

    def foreign_bytes(self) -> list[bytes]:
        return [*super().foreign_bytes(), b'{"a": "not a number"}', b"[1, 2]"]


# Sliding: a value asked for keeps being served, one nobody asks for goes


def _cache(tier: str, clock: ManualClock) -> tuple[Cache, JsonCodec[Tenant] | None]:
    if tier == "process":
        return Cache(now=clock.now, random=lambda: 0.5), None
    return Cache(InMemoryKeyValue(now=clock.now), now=clock.now), JsonCodec(Tenant)


@pytest.mark.parametrize("tier", ["process", "memory"])
def test_a_sliding_value_asked_for_is_served_until_at_most_and_one_left_idle_goes(tier):
    """A session-like value: a user active every few minutes is never sent to the source
    again, one who left is — and a hot value is still recomputed after `at_most`."""
    clock = ManualClock(datetime(2026, 9, 29, 12, 0, tzinfo=UTC))
    cache, codec = _cache(tier, clock)
    policy = KeepPolicy[Tenant](
        freshness=Sliding(idle_for=timedelta(minutes=10), at_most=timedelta(hours=1))
    )
    computed: list[str] = []

    def ask(key: str) -> Tenant:
        def compute() -> Tenant:
            computed.append(key)
            return Tenant(name=key, version="v1")

        return cache.get_or_compute(key, compute, policy, codec)

    ask("active")
    ask("idle")
    for _ in range(11):  # 55 minutes, a hit every 5
        clock.advance(minutes=5)
        ask("active")
    assert computed == ["active", "idle"]

    clock.advance(minutes=6)  # 61 minutes: past at_most, however active
    ask("active")
    ask("idle")  # 61 minutes idle

    assert computed == ["active", "idle", "active", "idle"]


def test_lifetime_is_still_the_time_to_live_and_keep_policy_still_takes_it():
    """Code written against phase 1 keeps running."""
    ttl = Lifetime(ttl=timedelta(minutes=5))
    policy = KeepPolicy[Tenant](lifetime=ttl)  # pyright: ignore[reportCallIssue]

    assert Lifetime is TimeToLive
    assert policy.freshness == ttl
    assert policy.lifetime == ttl


# Eviction: the process tier is bounded


def test_the_process_tier_is_bounded_by_default():
    """An unbounded process tier is a memory leak: a pod killed days after a deploy."""
    cache = Cache()

    eviction = cache._process.eviction
    assert isinstance(eviction, Lru)
    assert eviction.max_entries == 10_000


def test_the_process_tier_holds_no_more_than_its_bound_and_lets_go_of_the_least_used():
    cache = Cache(eviction=Lru(max_entries=3))
    computed: list[int] = []

    def ask(key: int) -> int:
        def compute() -> int:
            computed.append(key)
            return key

        return cache.get_or_compute(key, compute)

    for key in (1, 2, 3):
        ask(key)
    ask(1)  # 1 is used again: 2 is now the least recently used
    ask(4)
    assert len(cache._process._kept) == 3

    ask(1)
    ask(3)
    ask(2)  # evicted: computed again
    assert computed == [1, 2, 3, 4, 2]


def test_the_bound_holds_under_callers_racing_on_many_keys():
    cache = Cache(eviction=Lru(max_entries=20))

    def ask(start: int) -> None:
        for key in range(start, start + 200):
            cache.get_or_compute(key, lambda: key)

    threads = [threading.Thread(target=ask, args=(n * 1000,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(cache._process._kept) <= 20
    assert len(cache._process.flight._locks) <= 20 + 8  # an evicted key's lock goes with it


def test_an_unbounded_tier_keeps_every_key():
    cache = Cache(eviction=Unbounded())
    for key in range(100):
        cache.get_or_compute(key, lambda: key)

    assert len(cache._process._kept) == 100


def test_an_lru_of_nothing_is_refused():
    with pytest.raises(ContractViolation):
        Lru(max_entries=0)
