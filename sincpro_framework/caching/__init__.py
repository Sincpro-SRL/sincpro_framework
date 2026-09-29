"""Caching as infrastructure: the port a provider implements, the strategies a kept value is
judged by, and what the framework builds on them.

    store = InMemoryKeyValue()                       # or RedisKeyValue, MemcachedKeyValue, yours

    Cache(store).get_or_compute(key, compute, KeepPolicy(Lifetime(...), ExternalVersion(...)))
    Idempotency(store).run(key, write, IdempotencyPolicy(expires_after=...), JsonCodec(Answer))
    QueryCaching(store).on(bus, Query, CachePolicy(...))     # a Query of the ORM, by what it read

`domain/` is the vocabulary — the store contract, the strategies, the policies, the refusals;
`adapters/` the stores, the codec and the key encoder; `infrastructure/` the non-functional
mechanics (one caller per key, bounded waits); `entrypoint/` what a composition uses — `Cache`,
`Idempotency`, `QueryCaching`. Providers are extras that never load unless
imported: `[redis]` (Redis and Valkey) and `[memcached]`. Nothing here but `QueryCaching` knows
about repositories.
"""

from sincpro_framework.caching.adapters.in_memory import InMemoryKeyValue
from sincpro_framework.caching.adapters.json_codec import JsonCodec
from sincpro_framework.caching.adapters.key_value_records import KeyValueRecords
from sincpro_framework.caching.adapters.keys import key_of
from sincpro_framework.caching.adapters.observers import (
    CountingObserver,
    NoObserver,
    SpanObserver,
)
from sincpro_framework.caching.domain.codec import Codec
from sincpro_framework.caching.domain.exceptions import (
    AlreadyInProgress,
    IdempotencyError,
    KeyReused,
)
from sincpro_framework.caching.domain.idempotency_records import (
    IdempotencyRecord,
    IdempotencyRecords,
    RecordState,
)
from sincpro_framework.caching.domain.idempotent_command import IdempotentCommand
from sincpro_framework.caching.domain.lifetime import Lifetime
from sincpro_framework.caching.domain.observer import (
    CacheObserver,
    CacheOutcome,
    IdempotencyOutcome,
)
from sincpro_framework.caching.domain.policies import (
    CachePolicy,
    IdempotencyPolicy,
    KeepPolicy,
)
from sincpro_framework.caching.domain.store import KeyValueStore
from sincpro_framework.caching.domain.validation import (
    ExternalVersion,
    Unconditional,
    Validation,
)
from sincpro_framework.caching.entrypoint.cache import Cache
from sincpro_framework.caching.entrypoint.idempotency import (
    Idempotency,
    current_idempotency_key,
)
from sincpro_framework.caching.entrypoint.query_caching import QueryCaching

__all__ = [
    "AlreadyInProgress",
    "Cache",
    "CacheObserver",
    "CacheOutcome",
    "CachePolicy",
    "Codec",
    "CountingObserver",
    "ExternalVersion",
    "Idempotency",
    "IdempotentCommand",
    "IdempotencyError",
    "IdempotencyOutcome",
    "IdempotencyPolicy",
    "IdempotencyRecord",
    "IdempotencyRecords",
    "InMemoryKeyValue",
    "JsonCodec",
    "KeepPolicy",
    "KeyReused",
    "KeyValueRecords",
    "KeyValueStore",
    "Lifetime",
    "NoObserver",
    "QueryCaching",
    "RecordState",
    "SpanObserver",
    "Unconditional",
    "Validation",
    "current_idempotency_key",
    "key_of",
]
