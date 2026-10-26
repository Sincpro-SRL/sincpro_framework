"""The in-memory store honours the whole `KeyValueStore` contract — the default a service and
its tests use until it needs a store its replicas share."""

import math
from datetime import UTC, datetime, timedelta

from sincpro_framework.data_layer.store import InMemoryKeyValue, KeyValueStore
from sincpro_framework.data_layer.store.adapters.in_memory import SWEEP_EVERY
from sincpro_framework.runtime.testing import KeyValueStoreContract, ManualClock


class TestInMemoryKeyValue(KeyValueStoreContract):
    def make_store(self) -> KeyValueStore:
        self.clock = ManualClock(datetime(2026, 9, 27, tzinfo=UTC))
        return InMemoryKeyValue(now=self.clock.now)

    def expire(self, store: KeyValueStore, seconds: float) -> None:
        self.clock.advance(seconds=math.ceil(seconds))


class TestABoundedInMemoryKeyValue(KeyValueStoreContract):
    """A bound the contract never reaches: the store behaves the same."""

    def make_store(self) -> KeyValueStore:
        self.clock = ManualClock(datetime(2026, 9, 27, tzinfo=UTC))
        return InMemoryKeyValue(now=self.clock.now, max_entries=1000)

    def expire(self, store: KeyValueStore, seconds: float) -> None:
        self.clock.advance(seconds=math.ceil(seconds))


def test_past_its_bound_the_least_recently_used_key_is_let_go():
    store = InMemoryKeyValue(max_entries=2)
    store.set("a", b"1")
    store.set("b", b"2")
    store.get("a")  # a is used again: b is now the oldest

    store.set("c", b"3")

    assert store.get_many(["a", "b", "c"]) == [b"1", None, b"3"]


def test_what_expired_goes_before_anything_live_is_let_go():
    clock = ManualClock(datetime(2026, 9, 27, tzinfo=UTC))
    store = InMemoryKeyValue(now=clock.now, max_entries=2)
    store.set("old", b"1")
    store.set("short", b"2", ttl=timedelta(seconds=1))
    clock.advance(seconds=2)

    store.set("new", b"3")

    assert store.get_many(["old", "new"]) == [b"1", b"3"]


def test_what_expired_and_is_never_read_again_is_swept():
    clock = ManualClock(datetime(2026, 9, 27, tzinfo=UTC))
    store = InMemoryKeyValue(now=clock.now)
    for n in range(SWEEP_EVERY - 1):
        store.set(f"fingerprint:{n}", b"1", ttl=timedelta(seconds=1))
    clock.advance(seconds=2)

    store.set("one more", b"1")

    assert len(store._values) == 1
