"""`Cache`: a value cached by its parameters, served while its freshness and its validation say so,
and recomputed by one caller at a time — for anything, not only a Query of the ORM.

    tenants = Cache()                                            # objects, in this process
    tenant = tenants.get_or_compute(
        ("tenant", token),
        lambda: registry.resolve(token),
        CachePolicy(validation=ExternalVersion(registry.version_of_tenant, version_of)),
    )

    shared = Cache(RedisKeyValue(client), namespace="catalog")   # bytes, shared by replicas
    catalog = shared.get_or_compute(
        ("catalog", tenant_id),
        load_catalog,
        CachePolicy(
            freshness=TimeToLive(ttl=timedelta(minutes=5), stale_for=timedelta(minutes=1)),
            failure=FailSafe(serve_for=timedelta(hours=1)),
        ),
        JsonCodec(Catalog),
    )

Context — the order a call is answered in (PRD_13 §6), and the one outcome each reports:

1. Switched off (`enabled = False`): computed, nothing cached, nothing reported.
2. On a shared store whose breaker is open, or that fails on a read or a lock: `BYPASSED`, and
   the call is answered on the process tier — coalesced there — from step 3.
3. A cached value inside its servable life (fresh, or within its stale window) is validated:
   trusted while its last check is younger than `trusted_for`, asked otherwise. One that does not
   hold is recomputed and never served, not even as a fallback (`INVALIDATED`); a validation that
   raises cannot tell, and the `FailurePolicy` decides.
4. Valid and fresh, not picked by XFetch: served as is, its freshness renewed on the hit when the
   `Freshness` says so (`HIT`).
5. Valid but expiring: the caller that leads recomputes it (`COMPUTED`); the others serve it as it
   is (`STALE`).
6. Missing: the leader computes it (`COMPUTED`), the others wait up to `wait_for_others` for it
   (`COALESCED`) and compute it themselves if it does not come.
7. Final: computing raised, a cached value that its validation did not reject is inside its last
   resort — the `FailurePolicy` serves it, stored again as fresh for `throttle_for` so the
   source is asked once per window (`FALLBACK`). Otherwise the error is the answer.

What is never cached: an exception, a value whose validation answered no mark, a value larger than
`max_bytes` in a shared store. A failure to write to the store never fails the call: the value is
answered and the breaker trips. The store tier stores bytes (a `Codec` is required); the process
tier holds the objects themselves, bounded by its `Eviction` — nothing is encoded, and nothing
leaves the process, which is what a value carrying a credential needs.
"""

import json
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from sincpro_framework.data_layer.caching.adapters.eviction import Lru
from sincpro_framework.data_layer.caching.adapters.keys import KeyEncoder
from sincpro_framework.data_layer.caching.adapters.observers import CACHE_OUTCOMES, Observers
from sincpro_framework.data_layer.caching.domain import exceptions
from sincpro_framework.data_layer.caching.domain.codec import Codec
from sincpro_framework.data_layer.caching.domain.eviction import Eviction
from sincpro_framework.data_layer.caching.domain.freshness import NEVER
from sincpro_framework.data_layer.caching.domain.observer import CacheObserver, CacheOutcome
from sincpro_framework.data_layer.caching.domain.policies import CachePolicy
from sincpro_framework.data_layer.caching.infrastructure.clock import utc_now
from sincpro_framework.data_layer.caching.infrastructure.flight import (
    LocalSingleFlight,
    StoreSingleFlight,
    awaited,
)
from sincpro_framework.data_layer.store import KeyValueStore, StoreBypass


@dataclass(frozen=True)
class _Cached:
    """The record of one cached value, mapped field by field to what the store holds."""

    value: Any
    mark: str
    born: float
    fresh_until: float
    stale_until: float
    last_resort_until: float
    checked_until: float
    took: float


def _finite(moment: float) -> float | None:
    """A moment as the store's JSON header writes it — `NEVER` (infinity) as `null`."""
    return None if moment == NEVER else moment


def _moment(held: float | None) -> float:
    """`_finite` read back — `null` as `NEVER`."""
    return NEVER if held is None else held


def _retained_for(last_resort_until: float, now: float) -> timedelta | None:
    """How long the store is told to retain a value — its whole timeline, last resort included, so
    a value is never gone while it may still be served."""
    if last_resort_until == NEVER:
        return None
    return timedelta(seconds=max(1.0, last_resort_until - now))


class _StoreFailed(Exception):
    """The shared store failed on a read or a lock: the call is answered on the process tier."""


class _ProcessTier:
    """Objects in this process, never encoded, bounded by the eviction."""

    def __init__(self, eviction: Eviction) -> None:
        """`eviction` bounds the tier; a `LocalSingleFlight` elects one leader per key."""
        self.eviction = eviction
        self._cached: dict[str, _Cached] = {}
        self._lock = threading.Lock()
        self.flight = LocalSingleFlight()

    def read(self, key: str) -> _Cached | None:
        """The record under `key`, the eviction told it was served — `None` when missing."""
        with self._lock:
            cached = self._cached.get(key)
        if cached is not None:
            self.eviction.touched(key)
        return cached

    def write(self, key: str, cached: _Cached, policy: CachePolicy[Any], now: float) -> None:
        """Stores `cached` and drops the eviction's victims — their records under the lock,
        their flight locks after it. No `max_bytes` here: nothing is encoded to measure."""
        with self._lock:
            self._cached[key] = cached
            victims = self.eviction.admitted(key)
            for victim in victims:
                self._cached.pop(victim, None)
        for victim in victims:
            self.flight.forget(victim)

    def renews(self, cached: _Cached, fresh_until: float, now: float) -> bool:
        """Always — renewing in memory costs nothing."""
        return True

    def forget(self, key: str) -> None:
        """Drops the record under `key` and stops the eviction tracking it."""
        with self._lock:
            self._cached.pop(key, None)
        self.eviction.forgotten(key)

    def lead(self, key: str, policy: CachePolicy[Any]) -> bool:
        """Whether this caller leads `key` in this process — the policy plays no part."""
        return self.flight.lead(key)

    def release(self, key: str) -> None:
        """Ends the lead; the flight lock is dropped too when nothing was cached — a
        computation that cached nothing would otherwise leave a lock per key outside the
        eviction's bound."""
        self.flight.release(key)
        with self._lock:
            cached = key in self._cached
        if not cached:
            self.flight.forget(key)

    def awaited(self, key: str, policy: CachePolicy[Any]) -> _Cached | None:
        """Waits on the leader's lock for up to `wait_for_others`, then reads what it cached."""
        self.flight.wait(key, policy.wait_for_others)
        return self.read(key)


class _StoreTier:
    """Bytes in a `KeyValueStore`: one line of JSON with the bookkeeping, then the value.

    Context: every store operation goes through the breaker — a read or a lock that fails is
    `_StoreFailed` (the call moves to the process tier), a write or a release that fails only
    trips it: the value is answered all the same."""

    def __init__(self, store: KeyValueStore, codec: Codec[Any], breaker: StoreBypass) -> None:
        """`breaker` is the `Cache`'s own, shared by every call; a tier is made per call."""
        self.store = store
        self.codec = codec
        self.breaker = breaker
        self.flight = StoreSingleFlight(store)

    def _held(self, key: str) -> bytes | None:
        """The raw bytes under `key`; a failing store trips the breaker — `_StoreFailed`."""
        try:
            return self.store.get(key)
        except Exception as error:
            self.breaker.trip()
            raise _StoreFailed() from error

    def read(self, key: str) -> _Cached | None:
        """Context: what this deploy cannot read — a shape an older one stored, a torn write, a
        record of an older layout — is a miss, recomputed and written over, never an error."""
        held = self._held(key)
        if held is None:
            return None
        header, _, raw = held.partition(b"\n")
        try:
            about = json.loads(header)
            return _Cached(
                value=self.codec.decode(raw),
                mark=about["mark"],
                born=about["born"],
                fresh_until=_moment(about["fresh_until"]),
                stale_until=_moment(about["stale_until"]),
                last_resort_until=_moment(about["last_resort_until"]),
                checked_until=about["checked_until"],
                took=about["took"],
            )
        except Exception:
            return None

    def write(self, key: str, cached: _Cached, policy: CachePolicy[Any], now: float) -> None:
        """Stores `cached` as a JSON header line and the encoded value.

            b'{"mark": "", "born": 1700000000.0, "fresh_until": 1700000300.0, ...}\\n<value>'

        1. The value encoded; over `max_bytes`, nothing is cached — the call still answers.
        2. The header written, `NEVER` as `null`.
        3. Set for the record's whole timeline, last resort included (`_retained_for`).
        Final: a store that fails the write only trips the breaker.
        """
        raw = self.codec.encode(cached.value)
        if len(raw) > policy.max_bytes:
            return
        header = json.dumps(
            {
                "mark": cached.mark,
                "born": cached.born,
                "fresh_until": _finite(cached.fresh_until),
                "stale_until": _finite(cached.stale_until),
                "last_resort_until": _finite(cached.last_resort_until),
                "checked_until": cached.checked_until,
                "took": cached.took,
            }
        ).encode()
        try:
            self.store.set(
                key, header + b"\n" + raw, _retained_for(cached.last_resort_until, now)
            )
        except Exception:
            self.breaker.trip()

    def renews(self, cached: _Cached, fresh_until: float, now: float) -> bool:
        """Context: renewing on every hit is a write per read on a shared store; renewed only
        once a quarter of the window has gone, a hot key costs a few writes per window."""
        return fresh_until - cached.fresh_until >= (fresh_until - now) / 4

    def forget(self, key: str) -> None:
        """Deletes `key` for every replica — a failure trips the breaker and is raised."""
        try:
            self.store.delete(key)
        except Exception:
            self.breaker.trip()
            raise

    def lead(self, key: str, policy: CachePolicy[Any]) -> bool:
        """Whether this caller leads `key` across replicas — the lock held five times
        `wait_for_others`, so a leader that dies frees it soon after the others gave up."""
        try:
            return self.flight.lead(key, policy.wait_for_others * 5)
        except Exception as error:
            self.breaker.trip()
            raise _StoreFailed() from error

    def release(self, key: str) -> None:
        """A lock that cannot be released expires on its own, after its hold."""
        try:
            self.flight.release(key)
        except Exception:
            self.breaker.trip()

    def awaited(self, key: str, policy: CachePolicy[Any]) -> _Cached | None:
        """Polls the store for up to `wait_for_others` until something is cached under `key`."""
        return awaited(lambda: self.read(key), policy.wait_for_others)


class _NearRecords:
    """Records a shared store holds, decoded in this process, each until its moment — bounded
    by the eviction."""

    def __init__(self, eviction: Eviction) -> None:
        """Nothing held; `eviction` bounds how many."""
        self.eviction = eviction
        self._held: dict[str, tuple[_Cached, float]] = {}
        self._lock = threading.Lock()

    def read(self, key: str, now: float) -> _Cached | None:
        """The record held under `key` while its moment has not come — `None` otherwise, a
        lapsed one let go."""
        with self._lock:
            held = self._held.get(key)
            if held is not None and now >= held[1]:
                del self._held[key]
                held = None
        if held is None:
            return None
        self.eviction.touched(key)
        return held[0]

    def write(self, key: str, cached: _Cached, until: float) -> None:
        """`cached` held until `until`, the eviction's victims let go."""
        with self._lock:
            self._held[key] = (cached, until)
            for victim in self.eviction.admitted(key):
                self._held.pop(victim, None)

    def forget(self, key: str) -> None:
        """Let go of the record under `key`."""
        with self._lock:
            self._held.pop(key, None)
        self.eviction.forgotten(key)


class _NearTier:
    """A store tier whose records are also held in this process for `near`: a read finds them
    here first, decoded, without asking the store; every write and forget goes to both.

    Context: the record keeps its own timeline, so a value held here is judged — fresh, stale,
    still valid — exactly as one read from the store; only the round trip and the decoding are
    saved. A value the store would not take (past `max_bytes`) is still held here."""

    def __init__(
        self, store: _StoreTier, near: _NearRecords, near_for: float, now: float
    ) -> None:
        """`near_for` seconds, from `now` on each write — the call's clock."""
        self.store = store
        self.near = near
        self.near_for = near_for
        self.now = now

    def read(self, key: str) -> _Cached | None:
        """Held here, else read from the store and held."""
        held = self.near.read(key, self.now)
        if held is not None:
            return held
        cached = self.store.read(key)
        if cached is not None:
            self.near.write(key, cached, self.now + self.near_for)
        return cached

    def write(self, key: str, cached: _Cached, policy: CachePolicy[Any], now: float) -> None:
        """Written to the store — as far as it takes it — and held here."""
        self.store.write(key, cached, policy, now)
        self.near.write(key, cached, now + self.near_for)

    def renews(self, cached: _Cached, fresh_until: float, now: float) -> bool:
        """As the store does: renewing is a write to it."""
        return self.store.renews(cached, fresh_until, now)

    def forget(self, key: str) -> None:
        """Let go of here and on the store."""
        self.near.forget(key)
        self.store.forget(key)

    def lead(self, key: str, policy: CachePolicy[Any]) -> bool:
        """The store's lock: one leader across replicas."""
        return self.store.lead(key, policy)

    def release(self, key: str) -> None:
        """The store's lock released."""
        self.store.release(key)

    def awaited(self, key: str, policy: CachePolicy[Any]) -> _Cached | None:
        """What the leader stored, held here once it arrives."""
        cached = self.store.awaited(key, policy)
        if cached is not None:
            self.near.write(key, cached, self.now + self.near_for)
        return cached


type _Tier = _ProcessTier | _StoreTier | _NearTier


class Cache:
    """A value cached by its parameters, on two tiers — see the module header for the order a
    call is answered in."""

    def __init__(
        self,
        store: KeyValueStore | None = None,
        namespace: str = "",
        now: Callable[[], datetime] = utc_now,
        random: Callable[[], float] = random.random,
        enabled: bool = True,
        observer: CacheObserver | None = None,
        eviction: Eviction | None = None,
        bypass_for: timedelta = timedelta(seconds=30),
    ) -> None:
        """`store` shares values across replicas — `None` holds them as objects in this process;
        `namespace` separates this cache's keys from others in the same store; `now` and
        `random` are the clock and the dice freshness is judged by — a `ManualClock.now` and a
        fixed roll in tests. `observer` is told the outcome of every call (by default on the
        active span and on `sincpro.cache.outcomes`); `eviction` bounds the process tier (`Lru(10_000)` by default); `bypass_for` is
        how long a shared store that failed is left alone before it is tried again.

            Cache()                                     ->  objects in this process, Lru(10 000)
            Cache(RedisKeyValue(redis), namespace="rates")
                ->  bytes shared by every replica under "cache:rates:value:<32 hex>"
            Cache(enabled=False)                        ->  every call computes, nothing cached

        Nothing is read or written here: the store is first touched by a call."""
        self.store = store
        self.namespace = namespace
        self.now = now
        self.random = random
        self.enabled = enabled
        self.observer: CacheObserver = observer or Observers.traced_and_measured(
            CACHE_OUTCOMES
        )
        self._process = _ProcessTier(eviction or Lru())
        self._near = _NearRecords(Lru())
        """The records of a shared store held decoded here, for a policy that says `near`."""
        self._breaker = StoreBypass(bypass_for, self._clock)

    def _key(self, key: Any, codec: Codec[Any] | None = None) -> str:
        """Context: on a shared store the codec's schema is part of the key — a deploy that
        changes the shape of a value reads a key of its own, never what an older one stored.
        """
        prefix = f"cache:{self.namespace}:" if self.namespace else "cache:"
        schema = "" if codec is None else codec.schema
        return f"{prefix}value:{KeyEncoder.key_of(key, schema)}"

    def _codec_required(self, codec: Codec[Any] | None) -> Codec[Any]:
        """`codec`, else `ProgrammingError` — a shared store holds bytes, it needs one."""
        if codec is None:
            raise exceptions.ProgrammingError(
                "a Cache on a shared store holds bytes: pass the Codec its values are encoded with"
            )
        return codec

    def _clock(self) -> float:
        """`now()` in epoch seconds — the unit every moment of a record is in."""
        return self.now().timestamp()

    def _tier(
        self, codec: Codec[Any] | None, policy: CachePolicy[Any] | None = None
    ) -> _Tier:
        """The process tier with no store; else a store tier bound to `codec` and breaker —
        its records held here too when `policy` says `near`."""
        if self.store is None:
            return self._process
        tier = _StoreTier(self.store, self._codec_required(codec), self._breaker)
        if policy is None or not policy.near:
            return tier
        return _NearTier(tier, self._near, policy.near.total_seconds(), self._clock())

    def _observe(self, outcome: CacheOutcome) -> None:
        """Tells the observer, under this cache's namespace — one that raises is ignored."""
        try:
            self.observer.observed(outcome, self.namespace)
        except Exception:
            return

    def _still_valid[T](
        self, tier: _Tier, key: str, cached: _Cached, policy: CachePolicy[T], now: float
    ) -> bool:
        """1. A check younger than `trusted_for` is believed.
        2. Final: the validation is asked; a check that held is trusted anew for `trusted_for`.
        """
        if now < cached.checked_until:
            return True
        if not policy.validation.holds(cached.mark):
            return False
        trusted_for = policy.validation.trusted_for.total_seconds()
        if trusted_for > 0:
            tier.write(key, replace(cached, checked_until=now + trusted_for), policy, now)
        return True

    def _renewed_on_hit[T](
        self, tier: _Tier, key: str, cached: _Cached, policy: CachePolicy[T], now: float
    ) -> None:
        """A sliding freshness serves a value for as long as it is asked for."""
        fresh_until = policy.freshness.on_hit(
            cached.born, cached.fresh_until, now, self.random()
        )
        if fresh_until is None or not tier.renews(cached, fresh_until, now):
            return
        stale_until = policy.freshness.stale_until(fresh_until)
        renewed = replace(
            cached,
            fresh_until=fresh_until,
            stale_until=stale_until,
            last_resort_until=stale_until + policy.failure.last_resort_for.total_seconds(),
        )
        tier.write(key, renewed, policy, now)

    def _fell_back[T](
        self,
        tier: _Tier,
        key: str,
        fallback: _Cached | None,
        policy: CachePolicy[T],
        error: Exception,
    ) -> tuple[T, CacheOutcome]:
        """Row 7: the last good value, stored again as fresh — and trusted — for `throttle_for`,
        never past its last resort; without one, or with a policy that does not handle `error`,
        the error is the answer."""
        now = self._clock()
        if (
            fallback is None
            or now >= fallback.last_resort_until
            or not policy.failure.handles(error)
        ):
            raise error
        until = min(
            now + policy.failure.throttle_for.total_seconds(), fallback.last_resort_until
        )
        throttled = replace(
            fallback,
            fresh_until=until,
            stale_until=until,
            checked_until=max(fallback.checked_until, until),
        )
        tier.write(key, throttled, policy, now)
        return fallback.value, CacheOutcome.FALLBACK

    def _computed[T](
        self, tier: _Tier, key: str, compute: Callable[[], T], policy: CachePolicy[T]
    ) -> T:
        """1. The mark the validation takes before computing.
        2. The value, timed — XFetch weighs early recomputation by how long it took.
        3. Final: cached with the mark the validation settles on; no mark, nothing cached.
        """
        try:
            before = policy.validation.before()
            started = time.perf_counter()
            value = compute()
            took = time.perf_counter() - started
            mark = policy.validation.after(value, before)
            if mark is None:
                return value
            now = self._clock()
            fresh_until = policy.freshness.fresh_until(now, self.random())
            stale_until = policy.freshness.stale_until(fresh_until)
            tier.write(
                key,
                _Cached(
                    value=value,
                    mark=mark,
                    born=now,
                    fresh_until=fresh_until,
                    stale_until=stale_until,
                    last_resort_until=stale_until
                    + policy.failure.last_resort_for.total_seconds(),
                    checked_until=now + policy.validation.trusted_for.total_seconds(),
                    took=took,
                ),
                policy,
                now,
            )
            return value
        finally:
            tier.release(key)

    def _led[T](
        self,
        tier: _Tier,
        key: str,
        compute: Callable[[], T],
        policy: CachePolicy[T],
        fallback: _Cached | None,
        outcome: CacheOutcome,
    ) -> tuple[T, CacheOutcome]:
        """The leader's computation answered with `outcome`; one that raises falls back on
        `fallback` (row 7)."""
        try:
            return self._computed(tier, key, compute, policy), outcome
        except Exception as error:
            return self._fell_back(tier, key, fallback, policy, error)

    def _missing[T](
        self,
        tier: _Tier,
        key: str,
        compute: Callable[[], T],
        policy: CachePolicy[T],
        cached: _Cached | None,
        fallback: _Cached | None,
        rejected: bool,
    ) -> tuple[T, CacheOutcome]:
        """Rows 6 and 6a: the leader computes; the others wait for its value, then compute.

        `cached` is what was read (expired or rejected), `fallback` what may stand in when
        computing fails — `None` for a rejected value, which is never served.

        1. This caller leads: it computes and caches (`COMPUTED`; `INVALIDATED` if rejected).
        2. Another leads: wait up to `wait_for_others` for what it caches.
           2.1 Something new arrived — not the very record read before — it is served
               (`COALESCED`, `INVALIDATED` when `rejected`).
        3. Nothing arrived: this caller computes too, caching nothing; a failure falls back.
        Final: the value and the outcome to report.
        """
        if tier.lead(key, policy):
            outcome = CacheOutcome.INVALIDATED if rejected else CacheOutcome.COMPUTED
            return self._led(tier, key, compute, policy, fallback, outcome)
        # What arrives must be what the leader cached: the invalid or expired value read above,
        # read again because nobody replaced it in time, is never served.
        arrived = tier.awaited(key, policy)
        if arrived is not None and arrived != cached:
            outcome = CacheOutcome.INVALIDATED if rejected else CacheOutcome.COALESCED
            return arrived.value, outcome
        try:
            value = compute()
        except Exception as error:
            return self._fell_back(tier, key, fallback, policy, error)
        return value, CacheOutcome.INVALIDATED if rejected else CacheOutcome.COMPUTED

    def _answered[T](
        self, tier: _Tier, key: str, compute: Callable[[], T], policy: CachePolicy[T]
    ) -> tuple[T, CacheOutcome]:
        """Rows 2 to 7 of PRD_13 §6 on one tier — see the module's context.

        1. Nothing cached, or past its stale window: `_missing`, with what was read as fallback.
        2. Validated (`_still_valid`); a validation that raises falls back on the cached value.
           2.1 It no longer holds: `_missing`, rejected — the value never stands in.
        3. Not expiring (fresh, not picked by XFetch): renewed on the hit when the freshness
           slides it, served as `HIT`.
        4. Expiring: the leader recomputes it (`COMPUTED`), the others serve it (`STALE`).
        Final: the value and the outcome to report.
        """
        cached = tier.read(key)
        now = self._clock()
        if cached is None or now >= cached.stale_until:
            return self._missing(tier, key, compute, policy, cached, cached, rejected=False)
        try:
            valid = self._still_valid(tier, key, cached, policy, now)
        except Exception as error:
            return self._fell_back(tier, key, cached, policy, error)
        if not valid:
            return self._missing(tier, key, compute, policy, cached, None, rejected=True)
        expiring = now >= cached.fresh_until or policy.freshness.recompute_early(
            cached.fresh_until, cached.took, now, self.random()
        )
        if not expiring:
            self._renewed_on_hit(tier, key, cached, policy, now)
            return cached.value, CacheOutcome.HIT
        if tier.lead(key, policy):
            return self._led(tier, key, compute, policy, cached, CacheOutcome.COMPUTED)
        return cached.value, CacheOutcome.STALE

    def get_or_compute[T](
        self,
        key: Any,
        compute: Callable[[], T],
        policy: CachePolicy[T] | None = None,
        codec: Codec[T] | None = None,
    ) -> T:
        """The value cached under `key` — its parameters, hashed — or `compute()`'s. See the
        module's context for the order it is answered in.

            cache.get_or_compute(("rates", "USD"), load_rates)   ->  {"BOB": 6.96}, COMPUTED
            cache.get_or_compute(("rates", "USD"), load_rates)   ->  {"BOB": 6.96}, HIT

        1. Switched off: `compute()`, nothing cached or reported.
        2. No store: answered on the process tier.
        3. A store whose breaker is closed: answered on the store tier — through the records
           held here first when the policy says `near`.
           3.1 It failed on a read or a lock (`_StoreFailed`): on to step 4.
        4. `BYPASSED` reported, then answered on the process tier under the same hashed key.
        Final: the value; the outcome of the tier that answered is reported.
        """

        if not self.enabled:
            return compute()
        policy = policy or CachePolicy()
        if self.store is None:
            value, outcome = self._answered(self._process, self._key(key), compute, policy)
            self._observe(outcome)
            return value
        tier = self._tier(codec, policy)
        hashed = self._key(key, codec)
        if not self._breaker.is_open():
            try:
                value, outcome = self._answered(tier, hashed, compute, policy)
                self._observe(outcome)
                return value
            except _StoreFailed:
                pass
        self._observe(CacheOutcome.BYPASSED)
        value, outcome = self._answered(self._process, hashed, compute, policy)
        self._observe(outcome)
        return value

    def forget(self, key: Any, codec: Codec[Any] | None = None) -> None:
        """Let go of the value cached under `key` — in this process, and on a shared store for
        every replica, where the key is found by the same codec it was encoded with.

        Context: a forget is an invalidation somebody relies on, so a store that fails it raises
        — a value left on the other replicas must be known — rather than being bypassed.

            cache.forget(("rate", "USD"))                  # no store: this process only
            cache.forget(("rate", "USD"), JsonCodec(Rate))
                ->  gone here and from "cache:rates:value:<key and Rate's schema>"

        Forgotten in this process first, then on the store; with a store and no codec it is
        `ProgrammingError`, before anything is forgotten."""
        if self.store is None:
            self._process.forget(self._key(key))
            return
        hashed = self._key(key, codec)
        store_tier = self._tier(codec)
        self._process.forget(hashed)
        self._near.forget(hashed)
        store_tier.forget(hashed)
