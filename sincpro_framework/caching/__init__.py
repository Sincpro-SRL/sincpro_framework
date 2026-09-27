"""Caching as infrastructure: the port a provider implements, and what the framework builds on it.

    store = InMemoryKeyValue()                       # or RedisKeyValue, MemcachedKeyValue, yours

`KeyValueStore` is the specification — six operations — and the core ships it with a store in
memory. Providers are extras that never load unless imported: `[redis]` (Redis and Valkey) and
`[memcached]`.
"""

from sincpro_framework.caching.adapters.in_memory import InMemoryKeyValue
from sincpro_framework.caching.queries import CachePolicy, QueryCaching
from sincpro_framework.caching.store import KeyValueStore

__all__ = ["CachePolicy", "InMemoryKeyValue", "KeyValueStore", "QueryCaching"]
