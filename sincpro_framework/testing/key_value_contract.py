"""`KeyValueStoreContract`: the behaviour every `KeyValueStore` owes, as a test class to inherit.

    class TestRedisKeyValue(KeyValueStoreContract):
        def make_store(self) -> KeyValueStore:
            return RedisKeyValue(fakeredis.FakeRedis())

pytest collects the inherited `test_*` methods; each one builds a fresh store. A store of
yours — DynamoDB, a table, a service — passes the same class, and then everything built on the
port (query caching, crons across replicas) works on it. Nothing here imports pytest.
"""

import threading
from datetime import timedelta

from sincpro_framework.caching.domain.store import KeyValueStore


class KeyValueStoreContract:
    """Inherit, answer `make_store` with a new, empty store, and run with pytest."""

    racers = 16
    """How many threads race for `add` and `increment` — the atomicity half of the contract."""

    def make_store(self) -> KeyValueStore:
        raise NotImplementedError("answer a new, empty store")

    def expire(self, store: KeyValueStore, seconds: float) -> None:
        """Let `seconds` pass for the store — a store with an injectable clock advances it;
        one that uses real time overrides this to sleep, or skips the expiry tests."""
        raise NotImplementedError("advance the store's clock, or sleep")

    def test_a_value_set_is_got_back(self) -> None:
        store = self.make_store()
        store.set("a", b"1")
        if store.get("a") != b"1":
            raise AssertionError("a value set was not got back")

    def test_a_missing_key_is_none(self) -> None:
        if self.make_store().get("missing") is not None:
            raise AssertionError("a missing key answered a value")

    def test_get_many_answers_in_the_order_asked_with_none_for_the_missing(self) -> None:
        store = self.make_store()
        store.set("a", b"1")
        store.set("c", b"3")
        if store.get_many(["a", "b", "c"]) != [b"1", None, b"3"]:
            raise AssertionError("get_many did not answer in order, None for the missing")

    def test_add_writes_only_when_the_key_is_absent(self) -> None:
        store = self.make_store()
        first, second = store.add("k", b"first"), store.add("k", b"second")
        if not first or second or store.get("k") != b"first":
            raise AssertionError("add wrote over a key that was there, or refused a new one")

    def test_only_one_of_many_racing_adds_wins(self) -> None:
        store = self.make_store()
        won: list[bool] = []
        lock = threading.Lock()

        def race() -> None:
            added = store.add("claim", b"x")
            with lock:
                won.append(added)

        threads = [threading.Thread(target=race) for _ in range(self.racers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        if won.count(True) != 1:
            raise AssertionError(f"{won.count(True)} racing adds won; exactly one must")

    def test_increment_starts_at_one_and_counts_every_racer(self) -> None:
        store = self.make_store()
        if store.increment("fresh") != 1:
            raise AssertionError("increment of a missing key did not answer 1")
        threads = [
            threading.Thread(target=store.increment, args=("counted",))
            for _ in range(self.racers)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        if store.increment("counted") != self.racers + 1:
            raise AssertionError("racing increments lost a count")

    def test_delete_removes_and_tolerates_a_missing_key(self) -> None:
        store = self.make_store()
        store.set("a", b"1")
        store.delete("a")
        store.delete("never-there")
        if store.get("a") is not None:
            raise AssertionError("a deleted key answered a value")

    def test_take_answers_the_value_once_and_leaves_nothing(self) -> None:
        store = self.make_store()
        store.set("code", b"1")
        if store.take("code") != b"1" or store.take("code") is not None:
            raise AssertionError("take did not answer the value once")
        if store.get("code") is not None or store.take("never-there") is not None:
            raise AssertionError("take left the value behind, or answered a missing key")

    def test_only_one_of_many_racing_takes_gets_the_value(self) -> None:
        store = self.make_store()
        store.set("ticket", b"x")
        taken: list[bytes | None] = []
        lock = threading.Lock()

        def race() -> None:
            value = store.take("ticket")
            with lock:
                taken.append(value)

        threads = [threading.Thread(target=race) for _ in range(self.racers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        if [one for one in taken if one is not None] != [b"x"]:
            raise AssertionError(f"{len([one for one in taken if one])} racing takes got it")

    def test_a_value_with_a_ttl_is_gone_after_it(self) -> None:
        store = self.make_store()
        store.set("short", b"1", ttl=timedelta(seconds=1))
        store.add("claimed", b"1", ttl=timedelta(seconds=1))
        store.set("kept", b"1")
        self.expire(store, 1.5)
        if store.get("short") is not None or not store.add("claimed", b"2"):
            raise AssertionError("a value outlived its ttl")
        if store.get("kept") != b"1":
            raise AssertionError("a value without a ttl expired")
