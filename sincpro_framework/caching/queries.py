"""`QueryCaching`: a Query's answer kept by whoever composes the bounded context — the use case
never knows — and let go of when an aggregate it read is written.

    caching = QueryCaching(RedisKeyValue(client), near=timedelta(seconds=5))
    caching.on(billing, QueryBalance, CachePolicy(ttl=timedelta(minutes=5), vary_by=("tenant_id",)))
    invalidate_on_commit(database, caching)                 # writes of this process
    Subscriber(..., caching.invalidated_by({InvoiceIssued: [Invoice]}))   # writes of others

Context — what each answer carries, and why:

- **Its key** is the bus, the Query and its values written canonically (a decimal by its value,
  a date in ISO), the hash of the response's JSON Schema — a deploy that changes the answer's
  shape never reads the old one — and the context keys the policy varies by. A context key
  named sensitive and not varied by is refused: two users never share an answer.
- **Its dependencies** are what it read: the repository notes every aggregate a unit of
  reading touched (`ddd.repositories.reads`), so nobody declares them. Each is a tag with a
  version in the store; invalidating a tag is one `increment`, and an answer whose tags moved
  is not served. An answer that read nothing, with no declared dependencies, is never kept —
  there would be nothing to let it go by.
- **Its lifetime** is a safety net, not the mechanism: the ttl spread by `jitter` so answers
  kept together do not expire together, an optional `stale_for` window served while one
  caller recomputes, and an early recomputation that grows likelier as expiry nears (XFetch)
  so a busy answer is refreshed before it lapses.
- **One caller recomputes** a missing or expiring answer — whoever wins the store's `add` on
  its lock — while the others serve the stale one or wait a moment for the new one.
- **The near cache** keeps answers in the process for `near`, and still checks their tag
  versions in the store, so an invalidation from another replica is seen at once.
"""

import hashlib
import json
import logging
import math
import random as random_module
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, get_type_hints

from pydantic import BaseModel, TypeAdapter

from sincpro_framework.caching.store import KeyValueStore
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.ddd.repositories.reads import aggregate_tag, noting_reads
from sincpro_framework.sincpro_abstractions import Feature
from sincpro_framework.use_bus import UseFramework

logger = logging.getLogger("sincpro_framework.caching")

EVERYTHING = "*"
"""The tag every answer carries — `invalidate()` with no aggregate lets go of all of them."""


@dataclass(frozen=True)
class CachePolicy:
    ttl: timedelta
    vary_by: tuple[str, ...] = ()
    """Context keys each answer is kept per — a tenant, a locale."""
    stale_for: timedelta = timedelta(0)
    """How long past its ttl an answer is still served while one caller recomputes it."""
    jitter: float = 0.1
    """The ttl is spread by up to this fraction, up or down."""
    early_expiry: float = 1.0
    """XFetch's β: how eagerly an answer about to expire is recomputed; 0 never early."""
    depends_on: tuple[type, ...] = ()
    """Aggregates the answer depends on beyond what the repository noted."""
    max_bytes: int = 1_048_576
    """An answer larger than this is answered and not kept."""
    wait_for_others: timedelta = timedelta(seconds=2)
    """How long a caller that did not win the lock waits for the answer the winner computes."""


@dataclass(frozen=True)
class _Cached:
    bus: UseFramework
    query: type
    policy: CachePolicy
    response: TypeAdapter[Any]
    schema: str


@dataclass(frozen=True)
class _Entry:
    value: str
    tags: dict[str, int]
    fresh_until: float
    stale_until: float
    took: float

    def encoded(self) -> bytes:
        return json.dumps(self.__dict__).encode()


def _entry(held: bytes | None) -> _Entry | None:
    return None if held is None else _Entry(**json.loads(held))


def _canonical(value: Any) -> Any:
    """A value written the one way two equal values are always written."""
    match value:
        case BaseModel():
            return _canonical(value.model_dump())
        case Decimal():
            return str(value.normalize())
        case datetime() | date():
            return value.isoformat()
        case Enum():
            return _canonical(value.value)
        case Mapping():
            return {str(key): _canonical(item) for key, item in sorted(value.items())}
        case set() | frozenset():
            return sorted((_canonical(item) for item in value), key=json.dumps)
        case list() | tuple():
            return [_canonical(item) for item in value]
    return value


def _declared_response(handler: type) -> Any:
    return get_type_hints(handler.execute).get("return")


def _tag_key(tag: str) -> str:
    return f"cache:tag:{tag}"


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class _Near:
    entries: dict[str, tuple[bytes, float]] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)


class QueryCaching:
    def __init__(
        self,
        store: KeyValueStore,
        near: timedelta | None = None,
        sensitive: tuple[str, ...] = (),
        now: Callable[[], datetime] = utc_now,
        random: Callable[[], float] = random_module.random,
    ) -> None:
        """`near` keeps answers in this process too; `sensitive` names context keys — a user,
        a session — no answer may be shared across unless its policy varies by them."""
        self.store = store
        self.near = near
        self.sensitive = sensitive
        self.now = now
        self.random = random
        self._cached: dict[type, _Cached] = {}
        self._near = _Near()

    def _moment(self) -> float:
        return self.now().timestamp()

    def _registration(self, query: type) -> _Cached:
        cached = self._cached.get(query)
        if cached is None:
            raise ContractViolation(
                f"{query.__name__} is not cached here — cache it with on()"
            )
        return cached

    def _key(self, cached: _Cached, dto: Any) -> str:
        context = cached.bus.current_context()
        exposed = [
            name
            for name in self.sensitive
            if name in context and name not in cached.policy.vary_by
        ]
        if exposed:
            raise ContractViolation(
                f"{cached.query.__name__} runs with {', '.join(exposed)} in its context and its "
                f"cache policy does not vary by it: one caller's answer would reach another — "
                f"add it to vary_by, or do not cache this query"
            )
        body = {
            "bus": cached.bus.name,
            "query": f"{cached.query.__module__}.{cached.query.__qualname__}",
            "schema": cached.schema,
            "values": _canonical(dto),
            "vary": {name: _canonical(context.get(name)) for name in cached.policy.vary_by},
        }
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode())
        return f"cache:query:{digest.hexdigest()[:32]}"

    def _versions(self, tags: Iterable[str]) -> dict[str, int]:
        names = sorted(tags)
        held = self.store.get_many([_tag_key(tag) for tag in names])
        return {
            tag: int(value) if value is not None else 0 for tag, value in zip(names, held)
        }

    def _valid(self, entry: _Entry) -> bool:
        return self._versions(entry.tags) == entry.tags

    def _held(self, key: str) -> _Entry | None:
        """The answer kept under `key` whose dependencies have not moved — near first."""
        if self.near is not None:
            with self._near.lock:
                near = self._near.entries.get(key)
            if near is not None and near[1] > self._moment():
                entry = _entry(near[0])
                if entry is not None and self._valid(entry):
                    return entry
        entry = _entry(self.store.get(key))
        if entry is None or not self._valid(entry):
            return None
        self._keep_near(key, entry)
        return entry

    def _keep_near(self, key: str, entry: _Entry) -> None:
        if self.near is None:
            return
        with self._near.lock:
            self._near.entries[key] = (
                entry.encoded(),
                self._moment() + self.near.total_seconds(),
            )

    def _recompute_early(self, cached: _Cached, entry: _Entry, now: float) -> bool:
        """XFetch: true with a probability that grows as `fresh_until` nears."""
        if cached.policy.early_expiry <= 0:
            return False
        roll = max(self.random(), 1e-300)
        return (
            now - entry.took * cached.policy.early_expiry * math.log(roll)
            >= entry.fresh_until
        )

    def _lifetime(self, policy: CachePolicy) -> float:
        spread = 1 + policy.jitter * (2 * self.random() - 1)
        return policy.ttl.total_seconds() * spread

    def _computed(
        self, cached: _Cached, key: str, dto: Any, call_next: Callable[[Any], Any]
    ) -> Any:
        """Run the use case noting what it reads, and keep the answer by those reads.

        1. The versions of the declared dependencies, before anything is read.
        2. The use case, inside a unit of reading.
        3. The versions of everything it read — a declared one that moved meanwhile means a
           write landed during the read, and the answer is not kept.
        4. Final: kept for its jittered ttl plus the stale window, here and near.
        """
        declared = {aggregate_tag(model) for model in cached.policy.depends_on} | {EVERYTHING}
        before = self._versions(declared)
        started = time.perf_counter()
        with noting_reads() as reads:
            answer = call_next(dto)
        took = time.perf_counter() - started
        if not reads and not cached.policy.depends_on:
            logger.warning(
                "%s read nothing a repository noted and declares no depends_on: answered, not kept",
                cached.query.__name__,
            )
            return answer
        versions = self._versions(reads | declared)
        if any(versions[tag] != version for tag, version in before.items()):
            return answer
        value = cached.response.dump_json(answer).decode()
        if len(value) > cached.policy.max_bytes:
            return answer
        now = self._moment()
        fresh_until = now + self._lifetime(cached.policy)
        entry = _Entry(
            value,
            versions,
            fresh_until,
            fresh_until + cached.policy.stale_for.total_seconds(),
            took,
        )
        kept_for = timedelta(seconds=max(1.0, entry.stale_until - now))
        self.store.set(key, entry.encoded(), kept_for)
        self._keep_near(key, entry)
        return answer

    def _refreshed(
        self, cached: _Cached, key: str, dto: Any, call_next: Callable[[Any], Any]
    ) -> Any:
        try:
            return self._computed(cached, key, dto, call_next)
        finally:
            self.store.delete(self.lock_key_of(key))

    def _awaited(self, key: str, policy: CachePolicy) -> _Entry | None:
        deadline = time.monotonic() + policy.wait_for_others.total_seconds()
        while time.monotonic() < deadline:
            entry = self._held(key)
            if entry is not None:
                return entry
            time.sleep(0.005)
        return None

    def _answer(self, cached: _Cached, dto: Any, call_next: Callable[[Any], Any]) -> Any:
        """1. A held answer still fresh, and not picked for early recomputation, is served.
        2. One past fresh but inside its stale window, or picked early, is recomputed by the
           caller that wins its lock; the others serve it as it is.
        3. Final: a missing one is computed by the winner; the others wait for it a moment,
           and compute it themselves if it does not come.
        """
        key = self._key(cached, dto)
        lock = self.lock_key_of(key)
        entry = self._held(key)
        now = self._moment()
        if entry is not None and now < entry.stale_until:
            expiring = now >= entry.fresh_until or self._recompute_early(cached, entry, now)
            if expiring and self.store.add(lock, b"1", cached.policy.wait_for_others * 5):
                return self._refreshed(cached, key, dto, call_next)
            return cached.response.validate_json(entry.value)
        if self.store.add(lock, b"1", cached.policy.wait_for_others * 5):
            return self._refreshed(cached, key, dto, call_next)
        awaited = self._awaited(key, cached.policy)
        if awaited is not None:
            return cached.response.validate_json(awaited.value)
        return call_next(dto)

    def on(self, bus: UseFramework, query: type, policy: CachePolicy) -> None:
        """Cache `query`'s answers on `bus` — its handler registered, the bus not built yet."""
        handler = bus.handler_of(query)
        if handler is None:
            raise TypeError(f"register the handler of {query.__name__} before caching it")
        response = _declared_response(handler)
        if response is None:
            raise TypeError(
                f"{handler.__name__}.execute declares no response type: declare it, so a kept "
                "answer can be read back as what the use case answers"
            )
        adapter = TypeAdapter(response)
        schema = json.dumps(adapter.json_schema(), sort_keys=True)
        cached = _Cached(
            bus, query, policy, adapter, hashlib.sha256(schema.encode()).hexdigest()[:12]
        )
        self._cached[query] = cached

        def cache(dto: Any, call_next: Callable[[Any], Any]) -> Any:
            return self._answer(cached, dto, call_next)

        cache.__qualname__ = f"cache_{query.__name__}"
        bus.interceptor(query)(cache)

    def lock_key_of(self, key: str) -> str:
        return f"{key}:lock"

    def lock_key(self, dto: Any) -> str:
        """The lock the caller recomputing this answer holds."""
        return self.lock_key_of(self._key(self._registration(type(dto)), dto))

    def depends_on(self, dto: Any) -> set[str]:
        """The tags the answer held for `dto` depends on — empty when none is held."""
        entry = _entry(self.store.get(self._key(self._registration(type(dto)), dto)))
        return set() if entry is None else set(entry.tags) - {EVERYTHING}

    def invalidate(self, target: type | None = None) -> None:
        """Let go of every answer that read `target` or a subclass of it — all, when none."""
        if target is None:
            self.store.increment(_tag_key(EVERYTHING))
            return
        for model in target.__mro__[:-1]:
            self.store.increment(_tag_key(aggregate_tag(model)))

    def invalidated_by(
        self, events: Mapping[type[DomainEvent], Iterable[type]], name: str = "query-caching"
    ) -> UseFramework:
        """A bus that hears `events` and lets go of what depends on the aggregates each names —
        hand it to the `Subscriber` beside the others, and a write another process published
        reaches this cache."""
        listener = UseFramework(name, log_after_execution=False)
        listener.add_dependency("caching", self)
        listener.add_dependency(
            "invalidates", {event: tuple(models) for event, models in events.items()}
        )

        @listener.feature(list(events))
        class Invalidate(Feature):
            caching: QueryCaching
            invalidates: dict[type, tuple[type, ...]]

            def execute(self, dto: DomainEvent) -> None:
                for model in self.invalidates.get(type(dto), ()):
                    self.caching.invalidate(model)

        return listener
