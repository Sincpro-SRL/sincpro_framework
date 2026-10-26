"""Caching as infrastructure: the strategies a stored value is judged by, and what the framework
builds on them — on any `KeyValueStore` of `sincpro_framework.data_layer.store`.

    store = InMemoryKeyValue()            # sincpro_framework.data_layer.store — or RedisKeyValue,
                                          # MemcachedKeyValue, DynamoKeyValue, yours

    Cache(store).get_or_compute(
        key, compute, CachePolicy(freshness=TimeToLive(...)), JsonCodec(Answer)
    )

    Idempotency(store).run(key, write, IdempotencyPolicy(expires_after=...), JsonCodec(Answer))
    QueryCaching(store).on(bus, Query, QueryCachePolicy(...))     # a Query of the ORM, by what it read

    domain/          the vocabulary — the strategies (`Freshness`, `Validation`, `FailurePolicy`,
                     `Eviction`), the policies, the refusals
    adapters/        the records, the codec, the evictions, the observers, the `KeyEncoder`
    infrastructure/  the non-functional mechanics — one caller per key, bounded waits, the clock
    services/        the operations — `Cache`, `Idempotency`, `QueryCaching`
    entrypoint/      the facades a bus hands out (`bus.cache`, `bus.idempotency`, a
                     `ConfiguredCache`) and the bus mixin; they delegate to the services

A project builds its own cache as a dependency (`Cache(store)`), or takes the bus's (`bus.cache`):
in memory by default, judged by the default strategies, declared with decorators. Every store
operation is on `KeyValueStore` alone; the store engines and `StoreBypass`, the breaker of a store
that does not answer, are the store's (`sincpro_framework.data_layer.store`). The contract
suites a strategy of a project's passes live in `sincpro_framework.runtime.testing`. Nothing here
but `QueryCaching` knows about repositories.
"""

from sincpro_framework.data_layer.caching.adapters.eviction import Lru, Unbounded
from sincpro_framework.data_layer.caching.adapters.json_codec import JsonCodec
from sincpro_framework.data_layer.caching.adapters.key_value_records import (
    KeyValueRecords,
)
from sincpro_framework.data_layer.caching.adapters.keys import KeyEncoder
from sincpro_framework.data_layer.caching.adapters.observers import (
    CountingObserver,
    MetricsObserver,
    NoObserver,
    Observers,
    SpanObserver,
)
from sincpro_framework.data_layer.caching.domain.codec import Codec
from sincpro_framework.data_layer.caching.domain.eviction import Eviction
from sincpro_framework.data_layer.caching.domain.exceptions import (
    AlreadyInProgress,
    KeyReused,
)
from sincpro_framework.data_layer.caching.domain.failure import (
    FailSafe,
    FailurePolicy,
    Raise,
)
from sincpro_framework.data_layer.caching.domain.freshness import (
    NEVER,
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
from sincpro_framework.data_layer.caching.domain.observer import (
    CacheObserver,
    CacheOutcome,
    IdempotencyOutcome,
)
from sincpro_framework.data_layer.caching.domain.policies import (
    CachePolicy,
    IdempotencyPolicy,
    QueryCachePolicy,
)
from sincpro_framework.data_layer.caching.domain.validation import (
    ExternalVersion,
    Unconditional,
    Validation,
)
from sincpro_framework.data_layer.caching.entrypoint.facades import (
    CacheFacade,
    ConfiguredCache,
    IdempotencyFacade,
)
from sincpro_framework.data_layer.caching.services.cache import Cache
from sincpro_framework.data_layer.caching.services.idempotency import Idempotency
from sincpro_framework.data_layer.caching.services.query_caching import QueryCaching

__all__ = [
    "NEVER",
    "AlreadyInProgress",
    "Cache",
    "CacheFacade",
    "ConfiguredCache",
    "CacheObserver",
    "CacheOutcome",
    "QueryCachePolicy",
    "Codec",
    "CountingObserver",
    "Eviction",
    "ExternalVersion",
    "FailSafe",
    "FailurePolicy",
    "Freshness",
    "IDEMPOTENCY_KEY",
    "Idempotency",
    "IdempotencyFacade",
    "IdempotencyOutcome",
    "IdempotencyPolicy",
    "IdempotencyRecord",
    "IdempotencyRecords",
    "IdempotentCommand",
    "JsonCodec",
    "CachePolicy",
    "KeyReused",
    "KeyEncoder",
    "KeyValueRecords",
    "Lru",
    "MetricsObserver",
    "NoObserver",
    "Observers",
    "QueryCaching",
    "Raise",
    "RecordState",
    "Sliding",
    "SpanObserver",
    "TimeToLive",
    "Unbounded",
    "Unconditional",
    "Validation",
]
