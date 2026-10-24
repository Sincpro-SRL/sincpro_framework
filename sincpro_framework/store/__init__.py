"""The store — where the service keeps what it keeps outside the process: the contexts, the drafts
and pipeline runs beside them, the leases, the circuits, the caches and the idempotency records.

    from sincpro_framework.store import store_context_in, RedisKeyValue

    store_context_in(RedisKeyValue(redis))              the whole service, every replica
    billing.store_context_in(KeyValueContexts(...))     one bounded context, a store of its own
    current_store()                                     the running bus's, else the process's,
                                                        else this process's memory

**One store, many consumers.** The context keeps its nodes here, caching and idempotency their
values and records, circuits their state, pipelines their runs — each in a section of its own on
the same `KeyValueStore`. None of them owns the store; each asks here where it is.

    domain/          KeyValueStore (the port every engine implements), ContextStore and Entry,
                     what the store raises
    adapters/        InMemoryKeyValue, RedisKeyValue (Redis, Valkey), MemcachedKeyValue,
                     DynamoKeyValue, InMemoryContexts, KeyValueContexts and their entries
    infrastructure/  Leases — a name held by one owner at a time, on any KeyValueStore
    entrypoint/      store_context_in, process_store, current_store, key_value_store_of
    mixins/          what a bus adds: bus.store(), bus.cache, bus.idempotency

Every name below is loaded when first asked for: the engines are extras (`[redis]`,
`[memcached]`, `[aws]`) that never load unless asked, and the context imports this package while
the resolution here reads the context — loading lazily keeps that from being a cycle.
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sincpro_framework.store.adapters.dynamodb import DynamoKeyValue
    from sincpro_framework.store.adapters.in_memory import InMemoryKeyValue
    from sincpro_framework.store.adapters.memcached import MemcachedKeyValue
    from sincpro_framework.store.adapters.redis import RedisKeyValue
    from sincpro_framework.store.adapters.stores import InMemoryContexts, KeyValueContexts
    from sincpro_framework.store.domain.exceptions import (
        EntryConflict,
        LeaseLost,
        LeaseTaken,
        StoreUnavailable,
    )
    from sincpro_framework.store.domain.key_value import KeyValueStore
    from sincpro_framework.store.domain.store import ContextStore, Entry
    from sincpro_framework.store.entrypoint.resolution import (
        current_store,
        key_value_store_of,
        process_store,
        store_context_in,
    )
    from sincpro_framework.store.infrastructure.leases import Lease, Leases, LeaseState

_WHERE = {
    "DynamoKeyValue": "adapters.dynamodb",
    "InMemoryKeyValue": "adapters.in_memory",
    "MemcachedKeyValue": "adapters.memcached",
    "RedisKeyValue": "adapters.redis",
    "InMemoryContexts": "adapters.stores",
    "KeyValueContexts": "adapters.stores",
    "EntryConflict": "domain.exceptions",
    "LeaseLost": "domain.exceptions",
    "LeaseTaken": "domain.exceptions",
    "StoreUnavailable": "domain.exceptions",
    "KeyValueStore": "domain.key_value",
    "ContextStore": "domain.store",
    "Entry": "domain.store",
    "current_store": "entrypoint.resolution",
    "key_value_store_of": "entrypoint.resolution",
    "process_store": "entrypoint.resolution",
    "store_context_in": "entrypoint.resolution",
    "Lease": "infrastructure.leases",
    "LeaseState": "infrastructure.leases",
    "Leases": "infrastructure.leases",
}

__all__ = [
    "ContextStore",
    "DynamoKeyValue",
    "Entry",
    "EntryConflict",
    "InMemoryContexts",
    "InMemoryKeyValue",
    "KeyValueContexts",
    "KeyValueStore",
    "Lease",
    "LeaseLost",
    "LeaseState",
    "LeaseTaken",
    "Leases",
    "MemcachedKeyValue",
    "RedisKeyValue",
    "StoreUnavailable",
    "current_store",
    "key_value_store_of",
    "process_store",
    "store_context_in",
]


def __getattr__(name: str) -> Any:
    """A public name loaded the first time it is asked for, then kept on the module so the next
    read is a plain attribute.

        from sincpro_framework.store import KeyValueStore   imports domain.key_value now
        sincpro_framework.store.Nope                        AttributeError
    """
    where = _WHERE.get(name)
    if where is None:
        raise AttributeError(f"module 'sincpro_framework.store' has no attribute {name!r}")
    value = getattr(import_module(f"sincpro_framework.store.{where}"), name)
    globals()[name] = value
    return value
