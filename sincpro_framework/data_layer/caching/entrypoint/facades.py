"""The cache and the idempotency of a bounded context — and of the whole service — stored where
the context is stored, with nothing to build: one facade each, on every bus and global.

    @billing.cache                                             the default: a minute
    def exchange_rate(currency: str) -> Decimal: ...

    @billing.cache.ttl(timedelta(minutes=5))                   a configured cache, by its name
    @billing.cache.sliding(timedelta(minutes=30), timedelta(hours=8))
    @billing.cache.versioned(catalog.version)
    @billing.cache.ttl(timedelta(minutes=5), fail_safe=timedelta(hours=1))
    @billing.cache(CachePolicy(...))                            anything `Cache` can do

    billing.cache.queries(QueryBalance)                        a Query's answer, by its reads
    invalidate_on_commit(database, billing.cache.queries())    let go of when a write commits

    @billing.feature(CommandCharge)
    @billing.idempotency                                       once per request, for a day
    class Charge(Feature): ...

    @billing.idempotency.exactly_once(records)                 answered in the write's
                                                               transaction

    from sincpro_framework import cache, idempotency           the same, for the whole service

**Where it is stored is the context's**, read at every call: a bus's facade writes to the store
that bus persists its context in, else the process's, else this process's memory; the global ones
on the process's, else memory. Nothing is configured to start; `store_context_in(redis)`
shares it all.

**A configured cache is a decorator and a tool by hand.** An adapter never imports a bus: it is handed
the facade (`SiatClient(cache=billing.cache)`) and caches by hand:

    products = cache.ttl(timedelta(hours=1))
    products.get(("products", nit), lambda: soap.products(nit))
    products.forget(("products", nit))

**The key** is the facade's name (the bus's, or the global one), the function or the key
given, its arguments written canonically (`self` of a method left out), and whose it is — the
rule drafts and pipeline runs follow: the context's `tenant_id`, `tenant_ids` and `user_id`,
empty when it says nothing, plus the keys of `vary_by`. On a store replicas share an answer is
bytes, encoded by its function's return type; a function that declares none is cached in this
process only.

**Defaults never block**: a store that fails is bypassed and the call answered from this
process; a caller waits at most a second for a value another is computing, and is served the
old one while it is recomputed; a duplicate request in flight is refused at once.
"""

import asyncio
import functools
import inspect
import threading
from collections.abc import Callable, Sequence
from datetime import timedelta
from typing import Any, TypeVar, cast, get_type_hints
from weakref import WeakKeyDictionary

from sincpro_framework.context import ContextStore, Owner, use_context
from sincpro_framework.context.entrypoint.resolution import key_value_store_of, process_store
from sincpro_framework.data_layer.caching.adapters.json_codec import JsonCodec
from sincpro_framework.data_layer.caching.adapters.key_value_records import KeyValueRecords
from sincpro_framework.data_layer.caching.domain import exceptions
from sincpro_framework.data_layer.caching.domain.codec import Codec
from sincpro_framework.data_layer.caching.domain.failure import FailSafe, FailurePolicy, Raise
from sincpro_framework.data_layer.caching.domain.freshness import Sliding, TimeToLive
from sincpro_framework.data_layer.caching.domain.idempotency_records import IdempotencyRecords
from sincpro_framework.data_layer.caching.domain.policies import (
    CachePolicy,
    IdempotencyPolicy,
    QueryCachePolicy,
)
from sincpro_framework.data_layer.caching.domain.validation import ExternalVersion
from sincpro_framework.data_layer.caching.services.cache import Cache
from sincpro_framework.data_layer.caching.services.idempotency import Idempotency
from sincpro_framework.data_layer.caching.services.query_caching import QueryCaching
from sincpro_framework.data_layer.store import KeyValueStore
from sincpro_framework.sincpro_logger import logger

GLOBAL = "global"
"""The name the global facades store their keys under."""

DEFAULT_CACHE = CachePolicy[Any](
    freshness=TimeToLive(ttl=timedelta(seconds=60), stale_for=timedelta(seconds=10)),
    wait_for_others=timedelta(seconds=1),
)
"""What `@cache` caches by: a minute, the old answer served ten seconds more while one caller
recomputes it, a second at most waiting for a value another caller computes."""

WAIT_FOR_OTHERS = timedelta(seconds=1)
"""How long a caller waits for a value another is computing, in every cache configured by
name."""

DEFAULT_QUERIES = QueryCachePolicy(
    ttl=timedelta(seconds=60),
    stale_for=timedelta(seconds=10),
    wait_for_others=WAIT_FOR_OTHERS,
)
"""What `bus.cache.queries(Query)` caches an answer by: the same minute as `@cache` — let go of
sooner, when an aggregate it read is written."""

DEFAULT_EXPIRES_AFTER = timedelta(hours=24)
"""How long `@idempotency` replays an answer — a day, what clients that retry by key hold
their keys for."""

F = TypeVar("F", bound=Callable[..., Any])

_caches: WeakKeyDictionary[KeyValueStore, dict[str, Cache]] = WeakKeyDictionary()
"""One `Cache` per store and name: its process tier and its breaker outlive every call."""
_records: WeakKeyDictionary[KeyValueStore, KeyValueRecords] = WeakKeyDictionary()
_lock = threading.Lock()


def _cache_on(store: KeyValueStore, name: str) -> Cache:
    """The cache `name` on `store`: objects in this process's memory, bytes on a shared
    one."""
    with _lock:
        per_name = _caches.setdefault(store, {})
        cache = per_name.get(name)
        if cache is None:
            cache = per_name[name] = Cache(store if store.shared() else None, namespace=name)
        return cache


def _records_on(store: KeyValueStore) -> IdempotencyRecords:
    """The idempotency records on `store` — one per store, built the first time it is asked
    for, so every facade on that store shares them."""
    with _lock:
        records = _records.get(store)
        if records is None:
            records = _records[store] = KeyValueRecords(store)
        return records


def _is_method(function: Callable[..., Any]) -> bool:
    """Whether its first parameter is the instance or the class it is called on."""
    parameters = list(inspect.signature(function).parameters)
    return bool(parameters) and parameters[0] in ("self", "cls")


def _codec_of(function: Callable[..., Any]) -> Codec[Any] | None:
    """What its answer is encoded as on a shared store — `None` when it declares no return
    type."""
    try:
        returned = get_type_hints(function).get("return")
    except Exception:
        return None
    return None if returned is None else JsonCodec(returned)


def _keys(vary_by: str | Sequence[str]) -> tuple[str, ...]:
    """`vary_by` as a tuple of keys: `"language"  ->  ("language",)`."""
    return (vary_by,) if isinstance(vary_by, str) else tuple(vary_by)


def _failure(fail_safe: timedelta | FailurePolicy | None) -> FailurePolicy:
    """A failing source raises — or, given how long, the last good value stands in."""
    if fail_safe is None:
        return Raise()
    if isinstance(fail_safe, FailurePolicy):
        return fail_safe
    return FailSafe(serve_for=fail_safe)


class ConfiguredCache:
    """A facade's cache configured with one policy (`billing.cache.ttl(...)`): a decorator of
    functions, methods and coroutines, and a tool by hand — `get(key, compute)`,
    `forget(key)`. The strategies it is configured with are the policy's (`Freshness`,
    `Validation`, `FailurePolicy`)."""

    def __init__(
        self, facade: "CacheFacade", policy: CachePolicy[Any], vary_by: tuple[str, ...]
    ) -> None:
        """`facade`'s cache configured with `policy`, its owner read by the default keys plus
        `vary_by` — built by the facade (`billing.cache.ttl(...)`), not by hand.

            billing.cache.ttl(timedelta(minutes=5), vary_by="language")
                ->  ConfiguredCache(facade=billing.cache, vary_by=("language",)) — cached per
                    tenant, user and language, on the store the context answers at each call

        Beside it, a process-only `Cache` in the facade's namespace, for a value no codec
        could encode."""
        self.facade = facade
        self.policy = policy
        self.vary_by = vary_by
        self._in_process = Cache(None, namespace=facade.name)
        """Where a value is cached that a shared store could not take: no codec to encode it."""
        self._said: set[str] = set()

    def _owner(self) -> str:
        """Whose a value is, read from the context of the call: its tenant and user, and the
        keys of `vary_by`.

            vary_by=("language",), context {"tenant_id": "acme", "language": "es"}
            ->  "tenant_id=acme,tenant_ids=,user_id=,language=es"
        """
        return Owner.of(use_context(), (*Owner.BY_TENANT_AND_USER, *self.vary_by))

    def _cache(self, codec: Codec[Any] | None, about: str) -> tuple[Cache, Codec[Any] | None]:
        """The facade's cache on its store — or this process's, said once, when a shared store
        would need a codec there is none of."""
        cache = _cache_on(self.facade.store(), self.facade.name)
        if cache.store is None:
            return cache, None
        if codec is not None:
            return cache, codec
        if about not in self._said:
            self._said.add(about)
            logger.warning(
                f"{about}: no type to encode its value by, so it is cached in this process only — "
                "declare a return type, or pass a codec, to share it with the replicas"
            )
        return self._in_process, None

    def get[T](self, key: Any, compute: Callable[[], T], codec: Codec[T] | None = None) -> T:
        """The value cached under `key` — whose it is included — or `compute()`'s, cached. On a
        shared store `codec` encodes it; without one it is cached in this process only."""
        cache, used = self._cache(codec, f"cache {self.facade.name} key {key!r}")
        return cache.get_or_compute((self._owner(), key), compute, self.policy, used)

    def forget(self, key: Any, codec: Codec[Any] | None = None) -> None:
        """Let go of the value under `key` — here, and on every replica sharing the store."""
        cache, used = self._cache(codec, f"cache {self.facade.name} key {key!r}")
        cache.forget((self._owner(), key), used)

    def __call__(self, function: F) -> F:
        """`function` with its answer cached.

        1. A generator is refused: its values are produced as they are read — return a list.
        2. A coroutine is answered off the event loop: the cache waits on a thread while the
           coroutine runs on the loop, which is never blocked.
        Final: the function, its answer cached, with `forget(*args, **kwargs)`.
        """
        name = f"{function.__module__}.{function.__qualname__}"
        if inspect.isgeneratorfunction(function) or inspect.isasyncgenfunction(function):
            raise exceptions.ProgrammingError(
                f"cannot cache the answer of {name}: a generator's values are "
                "produced as they are read — return a list and cache that"
            )
        method = _is_method(function)
        codec = _codec_of(function)

        def key(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
            """The key of one call: the function's name, its arguments without `self` or
            `cls`, its keyword arguments sorted — `(name, [args], {kwargs})`."""
            values = args[1:] if method else args
            return (name, list(values), dict(sorted(kwargs.items())))

        def answer(args: tuple[Any, ...], kwargs: dict[str, Any], compute: Any) -> Any:
            """The value cached for this call and its owner — `compute()`'s when none is."""
            cache, used = self._cache(codec, name)
            owned = (self._owner(), key(args, kwargs))
            return cache.get_or_compute(owned, compute, self.policy, used)

        def forget(*args: Any, **kwargs: Any) -> None:
            """Let go of the value cached for these arguments — called without the instance on
            a method: `Rates.exchange_rate.forget("USD")`."""
            given = (None, *args) if method else args
            cache, used = self._cache(codec, name)
            cache.forget((self._owner(), key(given, kwargs)), used)

        if inspect.iscoroutinefunction(function):

            @functools.wraps(function)
            async def coroutine(*args: Any, **kwargs: Any) -> Any:
                """The coroutine's answer through the cache, waited for on a thread."""
                loop = asyncio.get_running_loop()

                def compute() -> Any:
                    """Run the coroutine on the caller's loop and wait for it — from the
                    cache's thread, never from the loop."""
                    running = asyncio.run_coroutine_threadsafe(
                        function(*args, **kwargs), loop
                    )
                    return running.result()

                return await asyncio.to_thread(answer, args, kwargs, compute)

            wrapped: Any = coroutine
        else:

            @functools.wraps(function)
            def plain(*args: Any, **kwargs: Any) -> Any:
                """The function's answer through the cache."""
                return answer(args, kwargs, lambda: function(*args, **kwargs))

            wrapped = plain
        wrapped.forget = forget
        return cast(F, wrapped)


class CacheFacade:
    """The cache of a bounded context (`billing.cache`) or of the whole service (`cache`)."""

    def __init__(
        self, name: str, store_of: Callable[[], ContextStore], bus: Any = None
    ) -> None:
        """`name` separates its keys from every other facade's on one store; `store_of`
        answers the context store it caches on, asked at every call; `bus` is the bounded
        context whose Queries it caches — none for the global one.

            CacheFacade("billing", billing.store, billing)   ->  `billing.cache`
            CacheFacade("global", process_store)             ->  `cache`, no Queries

        Its `QueryCaching` is built on first use, under the facade's lock."""
        self.name = name
        self.store_of = store_of
        self.bus = bus
        self._queries: QueryCaching | None = None
        self._lock = threading.Lock()

    def store(self) -> KeyValueStore:
        """The `KeyValueStore` it caches on now."""
        return key_value_store_of(self.store_of())

    def queries(
        self, query: type | None = None, policy: QueryCachePolicy | None = None
    ) -> QueryCaching:
        """The bounded context's `QueryCaching` — held on its store under its name — with
        `query`'s answers cached by `policy` (a minute, by default) when one is given.

            billing.cache.queries(QueryBalance)
            billing.cache.queries(QueryStatement, QueryCachePolicy(ttl=timedelta(minutes=5)))
            invalidate_on_commit(database, billing.cache.queries())

        1. The global cache holds no Query: a Query runs on its bus.
        2. One `QueryCaching` per facade, built at the first call: its tags and its flight are
           the same for every Query of the bounded context.
        Final: `query` cached on the bus — its handler registered first — and the caching
        answered.
        """
        if self.bus is None:
            raise exceptions.ProgrammingError(
                "the global cache holds no Query's answer: a Query runs on its bus — cache it "
                "with bus.cache.queries(Query)"
            )
        with self._lock:
            if self._queries is None:
                self._queries = QueryCaching(
                    namespace=self.name, store_of=self.store, bus=self.bus
                )
        if query is not None:
            self._queries.on(self.bus, query, policy or DEFAULT_QUERIES)
        return self._queries

    def __call__(
        self,
        function: Callable[..., Any] | CachePolicy[Any] | None = None,
        fail_safe: timedelta | FailurePolicy | None = None,
        vary_by: str | Sequence[str] = (),
    ) -> Any:
        """The default configured cache — or the one a `CachePolicy` says; a decorator used
        bare or called, or a configured cache to use by hand.

            @billing.cache                         a minute
            @billing.cache(vary_by="language")     the same, per a key of the project's
            @billing.cache(CachePolicy(...))       any freshness, validation, failure policy
            billing.cache().get(key, compute)      by hand
        """
        policy = function if isinstance(function, CachePolicy) else DEFAULT_CACHE
        if fail_safe is not None:
            policy = policy.model_copy(update={"failure": _failure(fail_safe)})
        configured = ConfiguredCache(self, policy, _keys(vary_by))
        return configured(function) if callable(function) else configured

    def _configured(
        self,
        policy: CachePolicy[Any],
        fail_safe: timedelta | FailurePolicy | None,
        vary_by: str | Sequence[str],
    ) -> ConfiguredCache:
        """A cache configured by name, on `policy` — with its failure policy and the wait every
        named configuration shares (`WAIT_FOR_OTHERS`)."""
        update = {"failure": _failure(fail_safe), "wait_for_others": WAIT_FOR_OTHERS}
        return ConfiguredCache(self, policy.model_copy(update=update), _keys(vary_by))

    def ttl(
        self,
        ttl: timedelta,
        stale_for: timedelta = timedelta(0),
        jitter: float = 0.1,
        fail_safe: timedelta | FailurePolicy | None = None,
        vary_by: str | Sequence[str] = (),
    ) -> ConfiguredCache:
        """Served for `ttl` — spread by `jitter` so values cached together do not expire
        together — and `stale_for` more while one caller recomputes it."""
        freshness = TimeToLive(ttl=ttl, stale_for=stale_for, jitter=jitter)
        return self._configured(CachePolicy(freshness=freshness), fail_safe, vary_by)

    def sliding(
        self,
        idle_for: timedelta,
        at_most: timedelta,
        fail_safe: timedelta | FailurePolicy | None = None,
        vary_by: str | Sequence[str] = (),
    ) -> ConfiguredCache:
        """Served while it is still asked for: `idle_for` after the last time, and never
        past `at_most` after it was computed."""
        freshness = Sliding(idle_for=idle_for, at_most=at_most)
        return self._configured(CachePolicy(freshness=freshness), fail_safe, vary_by)

    def versioned(
        self,
        current: Callable[[], str],
        version_of: Callable[[Any], str] | None = None,
        trusted_for: timedelta = timedelta(0),
        ttl: timedelta | None = None,
        fail_safe: timedelta | FailurePolicy | None = None,
        vary_by: str | Sequence[str] = (),
    ) -> ConfiguredCache:
        """Served while the source's version is the one it was computed at — `current` asks it
        (a cheap call), `version_of` reads it off the value, a check that held is trusted for
        `trusted_for`; `ttl` a safety net, none by default."""
        policy = CachePolicy[Any](
            freshness=TimeToLive(ttl=ttl),
            validation=ExternalVersion(
                current, version_of=version_of, trusted_for=trusted_for
            ),
        )
        return self._configured(policy, fail_safe, vary_by)


class IdempotencyFacade:
    """The idempotency of a bounded context (`billing.idempotency`) or of the whole
    service."""

    def __init__(self, name: str, store_of: Callable[[], ContextStore]) -> None:
        """`name` separates its keys from every other facade's on one store; `store_of`
        answers the context store its records are stored on, asked at every run.

            IdempotencyFacade("billing", billing.store)
                ->  `billing.idempotency`: records under the "billing" namespace, on the
                    `KeyValueStore` of whatever context store answers at each run

        The `Idempotency` of each `exactly_once` records object is built later, on its use."""
        self.name = name
        self.store_of = store_of
        self.idempotency = Idempotency(
            namespace=name,
            records_of=lambda: _records_on(key_value_store_of(self.store_of())),
        )
        """What runs the writes: an `Idempotency` whose records are the context's."""
        self._exactly: WeakKeyDictionary[IdempotencyRecords, Idempotency] = (
            WeakKeyDictionary()
        )
        self._lock = threading.Lock()

    def __call__(
        self,
        cls: Any = None,
        expires_after: timedelta = DEFAULT_EXPIRES_AFTER,
        in_progress_for: timedelta = timedelta(minutes=1),
        wait_for_completion: timedelta = timedelta(0),
        vary_by: str | Sequence[str] = (),
    ) -> Any:
        """Run a Feature or ApplicationService once per request — `@billing.idempotency`, or
        with its parameters. See `IdempotencyPolicy` for each one."""
        declare = self.idempotency.once(
            expires_after, in_progress_for, wait_for_completion, vary_by
        )
        return declare(cls) if cls is not None else declare

    def exactly_once(
        self,
        records: IdempotencyRecords,
        expires_after: timedelta = DEFAULT_EXPIRES_AFTER,
        in_progress_for: timedelta = timedelta(minutes=1),
        wait_for_completion: timedelta = timedelta(0),
        vary_by: str | Sequence[str] = (),
    ) -> Any:
        """Run a Feature or ApplicationService once per request, its answer completed in the
        write's own transaction — `records` open it (`DatabaseIdempotencyRecords` on the
        database the use case writes to). The same defaults and parameters as the facade's.

            @billing.feature(CommandIssueInvoice)
            @billing.idempotency.exactly_once(DatabaseIdempotencyRecords(...))
            class IssueInvoice(Feature): ...
                ->  a second CommandIssueInvoice with the same key replays the first answer

        One `Idempotency` per `records` object, built once and held while the records live."""
        with self._lock:
            exact = self._exactly.get(records)
            if exact is None:
                exact = self._exactly[records] = Idempotency(records, namespace=self.name)
        return exact.once(expires_after, in_progress_for, wait_for_completion, vary_by)

    def run[T](
        self,
        key: Any,
        run: Callable[[], T],
        policy: IdempotencyPolicy,
        codec: Codec[T],
        payload: Any = None,
    ) -> T:
        """`run()` once for `key`, its answer replayed for every repeat — by hand."""
        return self.idempotency.run(key, run, policy, codec, payload)

    def policies(self) -> dict[str, IdempotencyPolicy]:
        """Each use case that declared it, by its full name, with its policy."""
        declared = self.idempotency.policies()
        for exact in list(self._exactly.values()):
            declared.update(exact.policies())
        return declared


cache = CacheFacade(GLOBAL, process_store)
"""The service's cache: the process's store, else memory."""

idempotency = IdempotencyFacade(GLOBAL, process_store)
"""The service's idempotency: the process's store, else memory."""
