"""`QueryCaching`: a Query's answer kept by whoever composes the bounded context — the use case
never knows — and let go of when an aggregate it read is written.

    billing.cache.queries(QueryBalance)                      # a bounded context's: nothing to build
    caching = QueryCaching()                                 # where the context keeps what it shares
    caching = QueryCaching(RedisKeyValue(client), near=timedelta(seconds=5))   # a store of its own
    caching.on(billing, QueryBalance, QueryCachePolicy(ttl=timedelta(minutes=5), vary_by="tenant_id"))
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
- **A namespace is a cache of its own** in a store others share: its answers and its tags
  carry it, so `invalidate()` in one bounded context never lets go of another's. Contexts that
  read the same aggregates and must see each other's writes share one namespace.
- **`enabled = False`** runs every query's use case, keeping and serving nothing — to rule the
  cache out while chasing a wrong answer, or in an incident.
"""

import hashlib
import json
import logging
import random
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, get_type_hints
from weakref import WeakKeyDictionary

from pydantic import TypeAdapter

from sincpro_framework.common.naming import registered_name
from sincpro_framework.data_layer.caching.adapters.keys import canonical
from sincpro_framework.data_layer.caching.domain import exceptions
from sincpro_framework.data_layer.caching.domain.policies import QueryCachePolicy
from sincpro_framework.data_layer.caching.infrastructure.flight import (
    StoreSingleFlight,
    awaited,
)
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.ddd.repositories.reads import aggregate_tag, noting_reads
from sincpro_framework.sincpro_abstractions import Feature
from sincpro_framework.store import KeyValueStore
from sincpro_framework.store.entrypoint.resolution import current_store, key_value_store_of
from sincpro_framework.use_bus import UseFramework

logger = logging.getLogger("sincpro_framework.data_layer.caching")

EVERYTHING = "*"
"""The tag every answer carries — `invalidate()` with no aggregate lets go of all of them."""


@dataclass(frozen=True)
class _Cached:
    """One Query cached by `on()`: its bus, its policy, and how its answer is read back."""

    bus: UseFramework
    query: type
    policy: QueryCachePolicy
    response: TypeAdapter[Any]
    schema: str


@dataclass(frozen=True)
class _Entry:
    """One kept answer: its JSON, the tag versions it was computed at, and its timeline."""

    value: str
    tags: dict[str, int]
    fresh_until: float
    stale_until: float
    took: float

    def encoded(self) -> bytes:
        """The entry as the JSON object the store keeps — fields by name.

            _Entry('{"total": 10}', {"*": 0, "Invoice": 4}, 1700000300.0, 1700000360.0, 0.02)
                .encoded()  ->  b'{"value": "{\\"total\\": 10}", "tags": {"*": 0, ...}, ...}'

        What `_entry` reads back.
        """
        return json.dumps(self.__dict__).encode()


def _entry(held: bytes | None) -> _Entry | None:
    """`_Entry.encoded` read back — `None` for nothing held."""
    return None if held is None else _Entry(**json.loads(held))


def _declared_response(handler: type) -> Any:
    """The return annotation of `handler.execute` — `None` when it declares none."""
    return get_type_hints(handler.execute).get("return")


def utc_now() -> datetime:
    """The default clock of a `QueryCaching` — aware, in UTC."""
    return datetime.now(UTC)


@dataclass
class _Near:
    """The near cache: encoded entries in this process, each with the moment it lapses."""

    entries: dict[str, tuple[bytes, float]] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)


class QueryCaching:
    """Query answers kept by their parameters and let go of by the aggregates they read — see
    the module header for what each answer carries."""

    def __init__(
        self,
        store: KeyValueStore | None = None,
        near: timedelta | None = None,
        sensitive: tuple[str, ...] = (),
        now: Callable[[], datetime] = utc_now,
        random: Callable[[], float] = random.random,
        namespace: str = "",
        enabled: bool = True,
        store_of: Callable[[], KeyValueStore] | None = None,
    ) -> None:
        """`store` keeps the answers and their tags — none, where the context keeps what it
        shares: `store_of` says which (a bus's `cache.queries` passes its own, so a write in
        another bounded context invalidates on the same store), else the running context's (the
        store of the bus a Query runs in, else the process's, else this process's memory), read
        at every call. `near` keeps answers in this process too; `sensitive` names context keys
        — a user, a session — no answer may be shared across unless its policy varies by them;
        `namespace` keeps this cache apart from others in the same store.

            QueryCaching(RedisKeyValue(redis), near=timedelta(seconds=5), namespace="billing")
                ->  answers on Redis, and for 5s in this process too
            QueryCaching(sensitive=("user_id",))
                ->  on the running context's store; an answer that does not vary by
                    `user_id` is never shared across users

        1. The store: the one given; else `store_of`; else the running context's
           (`key_value_store_of(current_store())`) — the last two asked at every call.
        2. The settings kept: namespace, enabled, near, sensitive, the clock and the dice.
        3. Nothing declared yet: no Query, no near entry, no single-flight per store.
        Final: the store is first touched by a call, never here.
        """
        self._given = store
        self._store_of = store_of or (lambda: key_value_store_of(current_store()))
        self.namespace = namespace
        self.enabled = enabled
        self.near = near
        self.sensitive = sensitive
        self.now = now
        self.random = random
        self._cached: dict[type, _Cached] = {}
        self._near = _Near()
        self._flights: WeakKeyDictionary[KeyValueStore, StoreSingleFlight] = (
            WeakKeyDictionary()
        )

    @property
    def store(self) -> KeyValueStore:
        """The store given, else where the context keeps what it shares."""
        return self._given if self._given is not None else self._store_of()

    @property
    def _flight(self) -> StoreSingleFlight:
        """Who recomputes an answer, coordinated on the store it is kept in."""
        store = self.store
        flight = self._flights.get(store)
        if flight is None:
            flight = self._flights[store] = StoreSingleFlight(store)
        return flight

    def _prefix(self) -> str:
        """Where this cache's keys start: `"cache:billing:"`; `"cache:"` with no namespace."""
        return f"cache:{self.namespace}:" if self.namespace else "cache:"

    def _tag_key(self, tag: str) -> str:
        """The key of a tag's version counter.

            _tag_key("Invoice")  ->  "cache:billing:tag:Invoice"

        Invalidating the tag is one `increment` on this key.
        """
        return f"{self._prefix()}tag:{tag}"

    def _moment(self) -> float:
        """`now()` in epoch seconds — the unit every moment of an entry is in."""
        return self.now().timestamp()

    def _registration(self, query: type) -> _Cached:
        """What `on()` registered for `query` — `ProgrammingError` when not cached here."""
        cached = self._cached.get(query)
        if cached is None:
            raise exceptions.ProgrammingError(
                f"{query.__name__} is not cached here — cache it with on()"
            )
        return cached

    def _key(self, cached: _Cached, dto: Any) -> str:
        """The key one answer is kept under — the Query's identity, its response schema, its
        values and the context keys it varies by, hashed.

            _key(cached, QueryBalance(customer_id="c1"))  ->  "cache:billing:query:9f2c…"

        1. Refused (`ProgrammingError`) when the running context holds a `sensitive` key the
           policy does not vary by — one caller's answer would reach another.
        2. The parts written canonically, as JSON with sorted keys.
        Final: the prefix and the first 32 hex characters of the sha256.
        """
        context = cached.bus.current_context()
        exposed = [
            name
            for name in self.sensitive
            if name in context and name not in cached.policy.vary_by
        ]
        if exposed:
            raise exceptions.ProgrammingError(
                f"{cached.query.__name__} runs with {', '.join(exposed)} in its context and its "
                f"cache policy does not vary by it: one caller's answer would reach another — "
                f"add it to vary_by, or do not cache this query"
            )
        body = {
            "query": registered_name(cached.query, cached.bus.name),
            "schema": cached.schema,
            "values": canonical(dto),
            "vary": {name: canonical(context.get(name)) for name in cached.policy.vary_by},
        }
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode())
        return f"{self._prefix()}query:{digest.hexdigest()[:32]}"

    def _versions(self, tags: Iterable[str]) -> dict[str, int]:
        """The current version of each tag, in one `get_many` — 0 for one never invalidated.

            _versions({"*", "Invoice"})  ->  {"*": 0, "Invoice": 4}

        The tags are sorted first, so the store is always asked in one order.
        """
        names = sorted(tags)
        held = self.store.get_many([self._tag_key(tag) for tag in names])
        return {
            tag: int(value) if value is not None else 0 for tag, value in zip(names, held)
        }

    def _valid(self, entry: _Entry) -> bool:
        """Whether no tag the entry depends on moved since it was kept — one store read."""
        return self._versions(entry.tags) == entry.tags

    def _keep_near(self, key: str, entry: _Entry) -> None:
        """Keeps `entry` in this process for `near` — nothing when there is no near cache. An
        entry past its moment is not removed, only overwritten by the next one kept under its
        key."""
        if self.near is None:
            return
        with self._near.lock:
            self._near.entries[key] = (
                entry.encoded(),
                self._moment() + self.near.total_seconds(),
            )

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

    def _recompute_early(self, cached: _Cached, entry: _Entry, now: float) -> bool:
        """XFetch: true with a probability that grows as `fresh_until` nears."""
        return cached.policy.lifetime.recompute_early(
            entry.fresh_until, entry.took, now, self.random()
        )

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
        lifetime = cached.policy.lifetime
        fresh_until = lifetime.fresh_until(now, self.random())
        entry = _Entry(value, versions, fresh_until, lifetime.stale_until(fresh_until), took)
        kept_for = timedelta(seconds=max(1.0, entry.stale_until - now))
        self.store.set(key, entry.encoded(), kept_for)
        self._keep_near(key, entry)
        return answer

    def _refreshed(
        self, cached: _Cached, key: str, dto: Any, call_next: Callable[[Any], Any]
    ) -> Any:
        """The leader's computation (`_computed`), its lock released whatever happens."""
        try:
            return self._computed(cached, key, dto, call_next)
        finally:
            self._flight.release(key)

    def _awaited(self, key: str, policy: QueryCachePolicy) -> _Entry | None:
        """Polls for a valid answer under `key` for up to `wait_for_others` — `None` if none
        came."""
        return awaited(lambda: self._held(key), policy.wait_for_others)

    def _answer(self, cached: _Cached, dto: Any, call_next: Callable[[Any], Any]) -> Any:
        """1. A held answer still fresh, and not picked for early recomputation, is served.
        2. One past fresh but inside its stale window, or picked early, is recomputed by the
           caller that wins its lock; the others serve it as it is.
        3. Final: a missing one is computed by the winner; the others wait for it a moment,
           and compute it themselves if it does not come.
        """
        if not self.enabled:
            return call_next(dto)
        key = self._key(cached, dto)
        hold = cached.policy.wait_for_others * 5
        entry = self._held(key)
        now = self._moment()
        if entry is not None and now < entry.stale_until:
            expiring = now >= entry.fresh_until or self._recompute_early(cached, entry, now)
            if expiring and self._flight.lead(key, hold):
                return self._refreshed(cached, key, dto, call_next)
            return cached.response.validate_json(entry.value)
        if self._flight.lead(key, hold):
            return self._refreshed(cached, key, dto, call_next)
        arrived = self._awaited(key, cached.policy)
        if arrived is not None:
            return cached.response.validate_json(arrived.value)
        return call_next(dto)

    def on(self, bus: UseFramework, query: type, policy: QueryCachePolicy) -> None:
        """Cache `query`'s answers on `bus` — its handler registered, the bus not built yet.
        Only a `QueryCachePolicy`: a `Cache`'s `CachePolicy` judges a value, not a Query's answer.

            caching.on(billing, QueryBalance, QueryCachePolicy(ttl=timedelta(minutes=5)))

        1. Refused (`ProgrammingError`) for any other policy.
        2. Refused (`TypeError`) when the handler is not registered, or its `execute` declares
           no response type — a kept answer is read back as that type.
        3. The response's JSON Schema hashed (12 hex) — part of every key, so a deploy that
           changes the answer's shape never reads an older one.
        Final: an interceptor `cache_<Query>` on `bus` answers every call through `_answer`.
        """
        if not isinstance(policy, QueryCachePolicy):
            raise exceptions.ProgrammingError(
                f"{query.__name__} is cached by a QueryCachePolicy, not a "
                f"{type(policy).__name__}: QueryCachePolicy(ttl=...)"
            )
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
            """The interceptor `query` runs through: answered by `_answer`."""
            return self._answer(cached, dto, call_next)

        cache.__qualname__ = f"cache_{query.__name__}"
        bus.interceptor(query)(cache)

    def policies(self) -> dict[str, QueryCachePolicy]:
        """Each cached Query, by its identity (`registered_name`), with the policy it is kept
        by."""
        return {
            registered_name(query, cached.bus.name): cached.policy
            for query, cached in self._cached.items()
        }

    def lock_key_of(self, key: str) -> str:
        """The lock key of an answer's key — on the store the answer is kept in.

            lock_key_of("cache:billing:query:9f2c…")  ->  "cache:billing:query:9f2c…:lock"

        The same suffix `StoreSingleFlight` takes the lead under.
        """
        return self._flight.lock_key(key)

    def lock_key(self, dto: Any) -> str:
        """The lock the caller recomputing this answer holds."""
        return self.lock_key_of(self._key(self._registration(type(dto)), dto))

    def depends_on(self, dto: Any) -> set[str]:
        """The tags the answer held for `dto` depends on — empty when none is held."""
        entry = _entry(self.store.get(self._key(self._registration(type(dto)), dto)))
        return set() if entry is None else set(entry.tags) - {EVERYTHING}

    def invalidate(self, target: type | None = None) -> None:
        """Let go of every answer that read `target` or one of its bases — all, when none.
        Context: a read is of one class, never of its subclasses, so the bases are only let go
        of in case a project reads through them; it costs one `increment` each.

            caching.invalidate(Invoice)   ->  the tags of Invoice and its bases move by one
            caching.invalidate()          ->  the `*` tag moves: every answer is let go of
        """

        if target is None:
            self.store.increment(self._tag_key(EVERYTHING))
            return
        for model in target.__mro__[:-1]:
            self.store.increment(self._tag_key(aggregate_tag(model)))

    def invalidated_by(
        self, events: Mapping[type[DomainEvent], Iterable[type]], name: str = "query-caching"
    ) -> UseFramework:
        """A bus that hears `events` and lets go of what depends on the aggregates each names —
        hand it to the `Subscriber` beside the others, and a write another process published
        reaches this cache.

            listener = caching.invalidated_by({InvoiceIssued: [Invoice],
                                               PaymentApplied: [Invoice, Customer]})
            listener(PaymentApplied(...))
                ->  every answer that read an Invoice or a Customer is let go of

        1. A bus of its own, `name`, that logs no line after each execution.
        2. Its dependencies: this caching, and each event mapped to its aggregates as a tuple.
        3. One Feature for every event class: an event heard invalidates each aggregate its
           class is mapped to (`invalidate(model)`).
        Final: the bus, built here and not yet subscribed to anything.
        """
        listener = UseFramework(name, log_after_execution=False)
        listener.add_dependency("caching", self)
        listener.add_dependency(
            "invalidates", {event: tuple(models) for event, models in events.items()}
        )

        @listener.feature(list(events))
        class Invalidate(Feature):
            """The listener's one Feature: every event heard lets go of its aggregates."""

            caching: QueryCaching
            invalidates: dict[type, tuple[type, ...]]

            def execute(self, dto: DomainEvent) -> None:
                """Invalidates each aggregate the event's class is mapped to."""
                for model in self.invalidates.get(type(dto), ()):
                    self.caching.invalidate(model)

        return listener
