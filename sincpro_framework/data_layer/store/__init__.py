"""The store — a key-value store any component stands on, whatever engine is behind it.

    from sincpro_framework.data_layer.store import KeyValueStore, RedisKeyValue, Leases

    store: KeyValueStore = RedisKeyValue(redis.Redis.from_url("redis://cache:6379/0"))
    store.add("lock:42", b"me", ttl=timedelta(seconds=30))     ->  True
    with Leases(store).held("sandbox:S-1", owner="worker-a"):  a name held across replicas
        ...

**Agnostic.** The store knows nothing of who uses it: the context saves its nodes, drafts and
pipeline runs on one, caching and idempotency their values and records, circuits their state, crons
their ticks — each under a prefix of its own, each built on `KeyValueStore` alone. Which store a
bounded context uses is the context's to say (`sincpro_framework.context.store_context_in`).

    domain/          KeyValueStore (the protocol every engine satisfies), Lease, LeaseState, what
                     the store raises
    adapters/        one engine each: InMemoryKeyValue, RedisKeyValue (Redis, Valkey),
                     MemcachedKeyValue, DynamoKeyValue
    infrastructure/  what the engines that reach a client share
    services/        Leases — a name held by one owner at a time, on any KeyValueStore — and
                     StoreBypass, the breaker of a store that does not answer

Every engine imports its library only when it is used, so this package imports with no extra
installed: `[redis]`, `[memcached]` and `[aws]` are asked for by the project that uses them.
"""

from sincpro_framework.data_layer.store.adapters.dynamodb import DynamoKeyValue
from sincpro_framework.data_layer.store.adapters.in_memory import InMemoryKeyValue
from sincpro_framework.data_layer.store.adapters.memcached import MemcachedKeyValue
from sincpro_framework.data_layer.store.adapters.redis import RedisKeyValue
from sincpro_framework.data_layer.store.domain.exceptions import (
    LeaseLost,
    LeaseTaken,
    StoreUnavailable,
)
from sincpro_framework.data_layer.store.domain.key_value import KeyValueStore
from sincpro_framework.data_layer.store.domain.lease import Lease, LeaseState
from sincpro_framework.data_layer.store.services.bypass import StoreBypass
from sincpro_framework.data_layer.store.services.leases import Leases

__all__ = [
    "DynamoKeyValue",
    "InMemoryKeyValue",
    "KeyValueStore",
    "Lease",
    "LeaseLost",
    "LeaseState",
    "LeaseTaken",
    "Leases",
    "MemcachedKeyValue",
    "RedisKeyValue",
    "StoreBypass",
    "StoreUnavailable",
]
