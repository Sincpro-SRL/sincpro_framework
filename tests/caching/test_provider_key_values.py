"""The Redis/Valkey and Memcached stores honour the same `KeyValueStore` contract as the one in
memory — driven by fakeredis and pymemcache's mock client, so no server is needed."""

import os
import socket
import time
import uuid
from datetime import timedelta

import fakeredis
import pytest
from pymemcache.client.base import PooledClient
from pymemcache.test.utils import MockMemcacheClient

from sincpro_framework.caching import KeyValueStore
from sincpro_framework.caching.adapters.memcached import MemcachedKeyValue
from sincpro_framework.caching.adapters.redis import RedisKeyValue
from sincpro_framework.testing import KeyValueStoreContract


class TestRedisKeyValue(KeyValueStoreContract):
    def make_store(self) -> KeyValueStore:
        return RedisKeyValue(fakeredis.FakeRedis(), prefix="test:")

    def expire(self, store: KeyValueStore, seconds: float) -> None:
        time.sleep(seconds)


class TestMemcachedKeyValue(KeyValueStoreContract):
    def make_store(self) -> KeyValueStore:
        return MemcachedKeyValue(MockMemcacheClient(), prefix="test:")

    def expire(self, store: KeyValueStore, seconds: float) -> None:
        time.sleep(seconds)

    @pytest.mark.skip(
        reason="pymemcache's MockMemcacheClient adds Python objects on incr, where memcached "
        "keeps decimal text; TestMemcachedKeyValueOnAServer runs it against a real one"
    )
    def test_increment_starts_at_one_and_counts_every_racer(self) -> None: ...

    @pytest.mark.skip(
        reason="pymemcache's MockMemcacheClient has no gets/cas; "
        "TestMemcachedKeyValueOnAServer runs take against a real one"
    )
    def test_take_answers_the_value_once_and_leaves_nothing(self) -> None: ...

    @pytest.mark.skip(
        reason="pymemcache's MockMemcacheClient has no gets/cas; "
        "TestMemcachedKeyValueOnAServer runs take against a real one"
    )
    def test_only_one_of_many_racing_takes_gets_the_value(self) -> None: ...


def _memcached_server() -> str | None:
    address = os.environ.get("SINCPRO_TEST_MEMCACHED", "localhost:11211")
    host, port = address.split(":")
    try:
        socket.create_connection((host, int(port)), timeout=0.2).close()
    except OSError:
        return None
    return address


@pytest.mark.skipif(
    _memcached_server() is None, reason="no memcached at SINCPRO_TEST_MEMCACHED"
)
class TestMemcachedKeyValueOnAServer(KeyValueStoreContract):
    def make_store(self) -> KeyValueStore:
        client = PooledClient(_memcached_server() or "", max_pool_size=32)
        client.flush_all()
        return MemcachedKeyValue(client, prefix=f"test-{uuid.uuid4().hex}:")

    def expire(self, store: KeyValueStore, seconds: float) -> None:
        time.sleep(seconds + 1)


def test_a_prefix_keeps_two_services_on_one_server_apart():
    server = fakeredis.FakeRedis()
    billing, catalog = RedisKeyValue(server, "billing:"), RedisKeyValue(server, "catalog:")

    billing.set("k", b"billing")

    assert catalog.get("k") is None and billing.get("k") == b"billing"


def test_memcached_rounds_a_ttl_under_a_second_up_to_one():
    store = MemcachedKeyValue(MockMemcacheClient())

    store.set("short", b"1", ttl=timedelta(milliseconds=200))
    assert store.get("short") == b"1"


class _CasClient:
    """pymemcache's surface `take` uses, with `cas` losing on demand — the race a real server
    decides, played out without one."""

    def __init__(self, lose_cas: bool) -> None:
        self.values: dict[str, bytes] = {}
        self.lose_cas = lose_cas

    def set(self, key, value, expire=0, noreply=False):  # type: ignore[no-untyped-def]
        self.values[key] = value
        return True

    def gets(self, key):  # type: ignore[no-untyped-def]
        return (self.values.get(key), b"1") if key in self.values else (None, None)

    def cas(self, key, value, cas, expire=0, noreply=False):  # type: ignore[no-untyped-def]
        if self.lose_cas:
            return False
        self.values[key] = value
        return True

    def delete(self, key, noreply=False):  # type: ignore[no-untyped-def]
        self.values.pop(key, None)
        return True


def test_memcached_take_answers_the_value_to_the_caller_whose_cas_wins():
    store = MemcachedKeyValue(_CasClient(lose_cas=False))
    store.set("code", b"1")

    assert store.take("code") == b"1"
    assert store.take("code") is None


def test_memcached_take_answers_nothing_to_a_caller_whose_cas_lost():
    """Another caller spent the value between this one's `gets` and its `cas`."""
    store = MemcachedKeyValue(_CasClient(lose_cas=True))
    store.set("code", b"1")

    assert store.take("code") is None
