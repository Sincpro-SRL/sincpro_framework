"""`Cache`: a value kept by its parameters, served while its freshness and its validation say so,
and recomputed by one caller at a time — for anything, not only a Query of the ORM.

    tenants = Cache()                                            # objects, in this process
    tenant = tenants.get_or_compute(
        ("tenant", token),
        lambda: registry.resolve(token),
        KeepPolicy(validation=ExternalVersion(registry.version_of_tenant, kept=version_of)),
    )

    shared = Cache(RedisKeyValue(client), namespace="catalog")   # bytes, shared by replicas
    catalog = shared.get_or_compute(
        ("catalog", tenant_id),
        load_catalog,
        KeepPolicy(
            freshness=TimeToLive(ttl=timedelta(minutes=5), stale_for=timedelta(minutes=1)),
            failure=FailSafe(serve_for=timedelta(hours=1)),
        ),
        JsonCodec(Catalog),
    )

Context — the order a call is answered in (PRD_13 §6), and the one outcome each reports:

1. Switched off (`enabled = False`): computed, nothing kept, nothing reported.
2. On a shared store whose breaker is open, or that fails on a read or a lock: `BYPASSED`, and
   the call is answered on the process tier — coalesced there — from step 3.
3. A kept value inside its servable life (fresh, or within its stale window) is validated:
   trusted while its last check is younger than `trusted_for`, asked otherwise. One that does not
   hold is recomputed and never served, not even as a fallback (`INVALIDATED`); a validation that
   raises cannot tell, and the `FailurePolicy` decides.
4. Valid and fresh, not picked by XFetch: served as is, its freshness renewed on the hit when the
   `Freshness` says so (`HIT`).
5. Valid but expiring: the caller that leads recomputes it (`COMPUTED`); the others serve it as it
   is (`STALE`).
6. Missing: the leader computes it (`COMPUTED`), the others wait up to `wait_for_others` for it
   (`COALESCED`) and compute it themselves if it does not come.
7. Final: computing raised, a value is kept that its validation did not reject, and it is inside
   its last resort — the `FailurePolicy` serves it, re-kept fresh for `throttle_for` so the
   source is asked once per window (`FALLBACK`). Otherwise the error is the answer.

What is never kept: an exception, a value whose validation answered no mark, a value larger than
`max_bytes` in a shared store. A failure to write to the store never fails the call: the value is
answered and the breaker trips. The store tier keeps bytes (a `Codec` is required); the process
tier keeps the objects themselves, bounded by its `Eviction` — nothing is encoded, and nothing
leaves the process, which is what a value carrying a credential needs.
"""

import json
import random as random_module
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from sincpro_framework.caching.adapters.eviction import Lru
from sincpro_framework.caching.adapters.keys import key_of
from sincpro_framework.caching.adapters.observers import CACHE_OUTCOMES, traced_and_measured
from sincpro_framework.caching.domain.codec import Codec
from sincpro_framework.caching.domain.eviction import Eviction
from sincpro_framework.caching.domain.freshness import NEVER
from sincpro_framework.caching.domain.observer import CacheObserver, CacheOutcome
from sincpro_framework.caching.domain.policies import KeepPolicy
from sincpro_framework.caching.domain.store import KeyValueStore
from sincpro_framework.caching.infrastructure.breaker import StoreBreaker
from sincpro_framework.caching.infrastructure.flight import LocalFlight, StoreFlight, awaited
from sincpro_framework.ddd.exceptions import ContractViolation


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class _Kept:
    """The record of one kept value, mapped field by field to what the store holds."""

    value: Any
    mark: str
    born: float
    fresh_until: float
    stale_until: float
    last_resort_until: float
    checked_until: float
    took: float


def _finite(moment: float) -> float | None:
    return None if moment == NEVER else moment


def _moment(held: float | None) -> float:
    return NEVER if held is None else held


def _kept_for(last_resort_until: float, now: float) -> timedelta | None:
    """What the store is told to keep a value for — its whole timeline, last resort included, so
    a value is never gone while it may still be served."""
    if last_resort_until == NEVER:
        return None
    return timedelta(seconds=max(1.0, last_resort_until - now))


class _StoreFailed(Exception):
    """The shared store failed on a read or a lock: the call is answered on the process tier."""


class _ProcessTier:
    """Objects in this process, never encoded, bounded by the eviction."""

    def __init__(self, eviction: Eviction) -> None:
        self.eviction = eviction
        self._kept: dict[str, _Kept] = {}
        self._lock = threading.Lock()
        self.flight = LocalFlight()

    def read(self, key: str) -> _Kept | None:
        with self._lock:
            kept = self._kept.get(key)
        if kept is not None:
            self.eviction.touched(key)
        return kept

    def write(self, key: str, kept: _Kept, policy: KeepPolicy[Any], now: float) -> None:
        with self._lock:
            self._kept[key] = kept
            victims = self.eviction.admitted(key)
            for victim in victims:
                self._kept.pop(victim, None)
        for victim in victims:
            self.flight.forget(victim)

    def renews(self, kept: _Kept, fresh_until: float, now: float) -> bool:
        return True

    def forget(self, key: str) -> None:
        with self._lock:
            self._kept.pop(key, None)
        self.eviction.forgotten(key)

    def lead(self, key: str, policy: KeepPolicy[Any]) -> bool:
        return self.flight.lead(key)

    def release(self, key: str) -> None:
        self.flight.release(key)
        with self._lock:
            kept = key in self._kept
        if not kept:
            self.flight.forget(key)

    def awaited(self, key: str, policy: KeepPolicy[Any]) -> _Kept | None:
        self.flight.wait(key, policy.wait_for_others)
        return self.read(key)


class _StoreTier:
    """Bytes in a `KeyValueStore`: one line of JSON with the bookkeeping, then the value.

    Context: every store operation goes through the breaker — a read or a lock that fails is
    `_StoreFailed` (the call moves to the process tier), a write or a release that fails only
    trips it: the value is answered all the same."""

    def __init__(
        self, store: KeyValueStore, codec: Codec[Any], breaker: StoreBreaker
    ) -> None:
        self.store = store
        self.codec = codec
        self.breaker = breaker
        self.flight = StoreFlight(store)

    def _held(self, key: str) -> bytes | None:
        try:
            return self.store.get(key)
        except Exception as error:
            self.breaker.trip()
            raise _StoreFailed() from error

    def read(self, key: str) -> _Kept | None:
        """Context: what this deploy cannot read — a shape an older one kept, a torn write, a
        record of an older layout — is a miss, recomputed and written over, never an error."""
        held = self._held(key)
        if held is None:
            return None
        header, _, raw = held.partition(b"\n")
        try:
            about = json.loads(header)
            return _Kept(
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

    def write(self, key: str, kept: _Kept, policy: KeepPolicy[Any], now: float) -> None:
        raw = self.codec.encode(kept.value)
        if len(raw) > policy.max_bytes:
            return
        header = json.dumps(
            {
                "mark": kept.mark,
                "born": kept.born,
                "fresh_until": _finite(kept.fresh_until),
                "stale_until": _finite(kept.stale_until),
                "last_resort_until": _finite(kept.last_resort_until),
                "checked_until": kept.checked_until,
                "took": kept.took,
            }
        ).encode()
        try:
            self.store.set(key, header + b"\n" + raw, _kept_for(kept.last_resort_until, now))
        except Exception:
            self.breaker.trip()

    def renews(self, kept: _Kept, fresh_until: float, now: float) -> bool:
        """Context: renewing on every hit is a write per read on a shared store; renewed only
        once a quarter of the window has gone, a hot key costs a few writes per window."""
        return fresh_until - kept.fresh_until >= (fresh_until - now) / 4

    def forget(self, key: str) -> None:
        try:
            self.store.delete(key)
        except Exception:
            self.breaker.trip()
            raise

    def lead(self, key: str, policy: KeepPolicy[Any]) -> bool:
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

    def awaited(self, key: str, policy: KeepPolicy[Any]) -> _Kept | None:
        return awaited(lambda: self.read(key), policy.wait_for_others)


type _Tier = _ProcessTier | _StoreTier


class Cache:
    def __init__(
        self,
        store: KeyValueStore | None = None,
        namespace: str = "",
        now: Callable[[], datetime] = utc_now,
        random: Callable[[], float] = random_module.random,
        enabled: bool = True,
        observer: CacheObserver | None = None,
        eviction: Eviction | None = None,
        bypass_for: timedelta = timedelta(seconds=30),
    ) -> None:
        """`store` shares values across replicas — `None` keeps them as objects in this process;
        `namespace` keeps this cache's keys apart from others in the same store; `now` and
        `random` are the clock and the dice freshness is judged by — a `ManualClock.now` and a
        fixed roll in tests. `observer` is told the outcome of every call (by default on the
        active span and on `sincpro.cache.outcomes`); `eviction` bounds the process tier (`Lru(10_000)` by default); `bypass_for` is
        how long a shared store that failed is left alone before it is tried again."""
        self.store = store
        self.namespace = namespace
        self.now = now
        self.random = random
        self.enabled = enabled
        self.observer: CacheObserver = observer or traced_and_measured(CACHE_OUTCOMES)
        self._process = _ProcessTier(eviction or Lru())
        self._breaker = StoreBreaker(bypass_for, self._clock)

    def _key(self, key: Any, codec: Codec[Any] | None = None) -> str:
        """Context: on a shared store the codec's schema is part of the key — a deploy that
        changes the shape of a value reads a key of its own, never what an older one kept."""
        prefix = f"cache:{self.namespace}:" if self.namespace else "cache:"
        schema = "" if codec is None else codec.schema
        return f"{prefix}value:{key_of(key, schema)}"

    def _tier(self, codec: Codec[Any] | None) -> _Tier:
        if self.store is None:
            return self._process
        return _StoreTier(self.store, self._codec_required(codec), self._breaker)

    def _clock(self) -> float:
        return self.now().timestamp()

    def _observe(self, outcome: CacheOutcome) -> None:
        try:
            self.observer.observed(outcome, self.namespace)
        except Exception:
            return

    def _still_valid[T](
        self, tier: _Tier, key: str, kept: _Kept, policy: KeepPolicy[T], now: float
    ) -> bool:
        """1. A check younger than `trusted_for` is believed.
        2. Final: the validation is asked; a check that held is trusted anew for `trusted_for`.
        """
        if now < kept.checked_until:
            return True
        if not policy.validation.holds(kept.mark):
            return False
        trusted_for = policy.validation.trusted_for.total_seconds()
        if trusted_for > 0:
            tier.write(key, replace(kept, checked_until=now + trusted_for), policy, now)
        return True

    def _renewed_on_hit[T](
        self, tier: _Tier, key: str, kept: _Kept, policy: KeepPolicy[T], now: float
    ) -> None:
        """A sliding freshness keeps a value served for as long as it is asked for."""
        fresh_until = policy.freshness.on_hit(kept.born, kept.fresh_until, now, self.random())
        if fresh_until is None or not tier.renews(kept, fresh_until, now):
            return
        stale_until = policy.freshness.stale_until(fresh_until)
        renewed = replace(
            kept,
            fresh_until=fresh_until,
            stale_until=stale_until,
            last_resort_until=stale_until + policy.failure.last_resort_for.total_seconds(),
        )
        tier.write(key, renewed, policy, now)

    def _fell_back[T](
        self,
        tier: _Tier,
        key: str,
        fallback: _Kept | None,
        policy: KeepPolicy[T],
        error: Exception,
    ) -> tuple[T, CacheOutcome]:
        """Row 7: the last good value, re-kept fresh — and trusted — for `throttle_for`, never
        past its last resort; without one, or with a policy that does not handle `error`, the
        error is the answer."""
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
        self, tier: _Tier, key: str, compute: Callable[[], T], policy: KeepPolicy[T]
    ) -> T:
        """1. The mark the validation takes before computing.
        2. The value, timed — XFetch weighs early recomputation by how long it took.
        3. Final: kept with the mark the validation settles on; no mark, nothing kept.
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
                _Kept(
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
        policy: KeepPolicy[T],
        fallback: _Kept | None,
        outcome: CacheOutcome,
    ) -> tuple[T, CacheOutcome]:
        try:
            return self._computed(tier, key, compute, policy), outcome
        except Exception as error:
            return self._fell_back(tier, key, fallback, policy, error)

    def _missing[T](
        self,
        tier: _Tier,
        key: str,
        compute: Callable[[], T],
        policy: KeepPolicy[T],
        kept: _Kept | None,
        fallback: _Kept | None,
        rejected: bool,
    ) -> tuple[T, CacheOutcome]:
        """Rows 6 and 6a: the leader computes; the others wait for its value, then compute."""
        if tier.lead(key, policy):
            outcome = CacheOutcome.INVALIDATED if rejected else CacheOutcome.COMPUTED
            return self._led(tier, key, compute, policy, fallback, outcome)
        # What arrives must be what the leader kept: the invalid or expired value read above,
        # read again because nobody replaced it in time, is never served.
        arrived = tier.awaited(key, policy)
        if arrived is not None and arrived != kept:
            outcome = CacheOutcome.INVALIDATED if rejected else CacheOutcome.COALESCED
            return arrived.value, outcome
        try:
            value = compute()
        except Exception as error:
            return self._fell_back(tier, key, fallback, policy, error)
        return value, CacheOutcome.INVALIDATED if rejected else CacheOutcome.COMPUTED

    def _answered[T](
        self, tier: _Tier, key: str, compute: Callable[[], T], policy: KeepPolicy[T]
    ) -> tuple[T, CacheOutcome]:
        """Rows 2 to 7 of PRD_13 §6 on one tier — see the module's context."""
        kept = tier.read(key)
        now = self._clock()
        if kept is None or now >= kept.stale_until:
            return self._missing(tier, key, compute, policy, kept, kept, rejected=False)
        try:
            valid = self._still_valid(tier, key, kept, policy, now)
        except Exception as error:
            return self._fell_back(tier, key, kept, policy, error)
        if not valid:
            return self._missing(tier, key, compute, policy, kept, None, rejected=True)
        expiring = now >= kept.fresh_until or policy.freshness.recompute_early(
            kept.fresh_until, kept.took, now, self.random()
        )
        if not expiring:
            self._renewed_on_hit(tier, key, kept, policy, now)
            return kept.value, CacheOutcome.HIT
        if tier.lead(key, policy):
            return self._led(tier, key, compute, policy, kept, CacheOutcome.COMPUTED)
        return kept.value, CacheOutcome.STALE

    def get_or_compute[T](
        self,
        key: Any,
        compute: Callable[[], T],
        policy: KeepPolicy[T] | None = None,
        codec: Codec[T] | None = None,
    ) -> T:
        """The value kept under `key` — its parameters, hashed — or `compute()`'s. See the
        module's context for the order it is answered in."""
        if not self.enabled:
            return compute()
        policy = policy or KeepPolicy()
        if self.store is None:
            value, outcome = self._answered(self._process, self._key(key), compute, policy)
            self._observe(outcome)
            return value
        tier = self._tier(codec)
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
        """Let go of the value kept under `key` — in this process, and on a shared store for
        every replica, where the key is found by the same codec it was kept with.

        Context: a forget is an invalidation somebody relies on, so a store that fails it raises
        — a value left on the other replicas must be known — rather than being bypassed."""
        if self.store is None:
            self._process.forget(self._key(key))
            return
        hashed = self._key(key, codec)
        store_tier = self._tier(codec)
        self._process.forget(hashed)
        store_tier.forget(hashed)

    def _codec_required(self, codec: Codec[Any] | None) -> Codec[Any]:
        if codec is None:
            raise ContractViolation(
                "a Cache on a shared store keeps bytes: pass the Codec its values are kept with"
            )
        return codec
