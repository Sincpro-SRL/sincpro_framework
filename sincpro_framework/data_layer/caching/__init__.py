"""Caching as infrastructure: the port a provider implements, the strategies a kept value is
judged by, and what the framework builds on them.

    store = InMemoryKeyValue()            # common.store — or RedisKeyValue, MemcachedKeyValue, yours

    Cache(store).get_or_compute(key, compute, KeepPolicy(TimeToLive(...), ExternalVersion(...)))
    Idempotency(store).run(key, write, IdempotencyPolicy(expires_after=...), JsonCodec(Answer))
    QueryCaching(store).on(bus, Query, CachePolicy(...))     # a Query of the ORM, by what it read

`domain/` is the vocabulary — the strategies (`Freshness`, `Validation`,
`FailurePolicy`, `Eviction`), the policies, the refusals; `adapters/` the stores, the codec, the
evictions and the key encoder; `infrastructure/` the non-functional mechanics (one caller per key,
bounded waits, the store breaker); `entrypoint/` what a composition uses — `Cache`,
`Idempotency`, `QueryCaching`. The contract suites a strategy of a project's passes live in
`sincpro_framework.runtime.testing`. Providers are extras that never load unless imported:
`[redis]` (Redis and Valkey) and `[memcached]`. Nothing here but `QueryCaching` knows
about repositories.
"""

from sincpro_framework.data_layer.caching.adapters.eviction import Lru, Unbounded
from sincpro_framework.data_layer.caching.adapters.json_codec import JsonCodec
from sincpro_framework.data_layer.caching.adapters.key_value_records import KeyValueRecords
from sincpro_framework.data_layer.caching.adapters.keys import key_of
from sincpro_framework.data_layer.caching.adapters.memcached import MemcachedKeyValue
from sincpro_framework.data_layer.caching.adapters.observers import (
    CountingObserver,
    MetricsObserver,
    NoObserver,
    Observers,
    SpanObserver,
)
from sincpro_framework.data_layer.caching.adapters.redis import RedisKeyValue
from sincpro_framework.data_layer.caching.domain.codec import Codec
from sincpro_framework.data_layer.caching.domain.eviction import Eviction
from sincpro_framework.data_layer.caching.domain.exceptions import (
    AlreadyInProgress,
    IdempotencyError,
    KeyReused,
)
from sincpro_framework.data_layer.caching.domain.failure import FailSafe, FailurePolicy, Raise
from sincpro_framework.data_layer.caching.domain.freshness import (
    Freshness,
    Sliding,
    TimeToLive,
)
from sincpro_framework.data_layer.caching.domain.idempotency_records import (
    IdempotencyRecord,
    IdempotencyRecords,
    RecordState,
)
from sincpro_framework.data_layer.caching.domain.idempotent_command import (
    IDEMPOTENCY_KEY,
    IdempotentCommand,
)
from sincpro_framework.data_layer.caching.domain.lifetime import Lifetime
from sincpro_framework.data_layer.caching.domain.observer import (
    CacheObserver,
    CacheOutcome,
    IdempotencyOutcome,
)
from sincpro_framework.data_layer.caching.domain.policies import (
    CachePolicy,
    IdempotencyPolicy,
    KeepPolicy,
)
from sincpro_framework.data_layer.caching.domain.validation import (
    ExternalVersion,
    Unconditional,
    Validation,
)
from sincpro_framework.data_layer.caching.entrypoint.cache import Cache
from sincpro_framework.data_layer.caching.entrypoint.idempotency import (
    Idempotency,
    current_idempotency_key,
    declares_once,
)
from sincpro_framework.data_layer.caching.entrypoint.query_caching import QueryCaching

__all__ = [
    "AlreadyInProgress",
    "Cache",
    "CacheObserver",
    "CacheOutcome",
    "CachePolicy",
    "Codec",
    "CountingObserver",
    "Eviction",
    "ExternalVersion",
    "FailSafe",
    "FailurePolicy",
    "Freshness",
    "IDEMPOTENCY_KEY",
    "Idempotency",
    "IdempotencyError",
    "IdempotencyOutcome",
    "IdempotencyPolicy",
    "IdempotencyRecord",
    "IdempotencyRecords",
    "IdempotentCommand",
    "JsonCodec",
    "KeepPolicy",
    "KeyReused",
    "KeyValueRecords",
    "Lifetime",
    "Lru",
    "MemcachedKeyValue",
    "MetricsObserver",
    "NoObserver",
    "Observers",
    "QueryCaching",
    "Raise",
    "RecordState",
    "RedisKeyValue",
    "Sliding",
    "SpanObserver",
    "TimeToLive",
    "Unbounded",
    "Unconditional",
    "Validation",
    "current_idempotency_key",
    "declares_once",
    "key_of",
]
