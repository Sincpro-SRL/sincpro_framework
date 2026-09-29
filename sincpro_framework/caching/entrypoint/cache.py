"""`Cache`: a value kept by its parameters, served while its lifetime and its validation say so,
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
        KeepPolicy(lifetime=Lifetime(ttl=timedelta(minutes=5), stale_for=timedelta(minutes=1))),
        JsonCodec(Catalog),
    )

Context — the order a call is answered in:

1. Switched off (`enabled = False`): computed, nothing kept.
2. A kept value inside its servable life (fresh, or within `stale_for`) is validated: trusted
   while its last check is younger than `trusted_for`, asked otherwise. One that does not hold
   is treated as missing — never served.
3. Valid and fresh, not picked by XFetch: served as is.
4. Valid but expiring: the caller that leads recomputes it; the others serve it as it is.
5. Final: missing — the leader computes it, the others wait up to `wait_for_others` for it, and
   compute it themselves if it does not come.

What is never kept: an exception, a value whose validation answered no mark, a value larger than
`max_bytes` in a shared store. The store tier keeps bytes (a `Codec` is required); the process
tier keeps the objects themselves — nothing is encoded, and nothing leaves the process, which is
what a value carrying a credential needs.
"""

import json
import random as random_module
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from sincpro_framework.caching.adapters.keys import key_of
from sincpro_framework.caching.domain.codec import Codec
from sincpro_framework.caching.domain.lifetime import NEVER
from sincpro_framework.caching.domain.policies import KeepPolicy
from sincpro_framework.caching.domain.store import KeyValueStore
from sincpro_framework.caching.infrastructure.flight import LocalFlight, StoreFlight, awaited
from sincpro_framework.ddd.exceptions import ContractViolation


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class _Kept:
    """The record of one kept value, mapped field by field to what the store holds."""

    value: Any
    mark: str
    fresh_until: float
    stale_until: float
    checked_until: float
    took: float


def _finite(moment: float) -> float | None:
    return None if moment == NEVER else moment


def _moment(held: float | None) -> float:
    return NEVER if held is None else held


class _ProcessTier:
    """Objects in this process, never encoded."""

    def __init__(self) -> None:
        self._kept: dict[str, _Kept] = {}
        self.flight = LocalFlight()

    def read(self, key: str) -> _Kept | None:
        return self._kept.get(key)

    def write(self, key: str, kept: _Kept, policy: KeepPolicy[Any], now: float) -> None:
        self._kept[key] = kept

    def forget(self, key: str) -> None:
        self._kept.pop(key, None)

    def lead(self, key: str, policy: KeepPolicy[Any]) -> bool:
        return self.flight.lead(key)

    def release(self, key: str) -> None:
        self.flight.release(key)

    def awaited(self, key: str, policy: KeepPolicy[Any]) -> _Kept | None:
        self.flight.wait(key, policy.wait_for_others)
        return self.read(key)


class _StoreTier:
    """Bytes in a `KeyValueStore`: one line of JSON with the bookkeeping, then the value."""

    def __init__(self, store: KeyValueStore, codec: Codec[Any]) -> None:
        self.store = store
        self.codec = codec
        self.flight = StoreFlight(store)

    def read(self, key: str) -> _Kept | None:
        """Context: what this deploy cannot read — a shape an older one kept, a torn write — is a
        miss, recomputed and written over, never an error handed to the caller."""
        held = self.store.get(key)
        if held is None:
            return None
        header, _, raw = held.partition(b"\n")
        try:
            about = json.loads(header)
            value = self.codec.decode(raw)
        except Exception:
            return None
        return _Kept(
            value=value,
            mark=about["mark"],
            fresh_until=_moment(about["fresh_until"]),
            stale_until=_moment(about["stale_until"]),
            checked_until=about["checked_until"],
            took=about["took"],
        )

    def write(self, key: str, kept: _Kept, policy: KeepPolicy[Any], now: float) -> None:
        raw = self.codec.encode(kept.value)
        if len(raw) > policy.max_bytes:
            return
        header = json.dumps(
            {
                "mark": kept.mark,
                "fresh_until": _finite(kept.fresh_until),
                "stale_until": _finite(kept.stale_until),
                "checked_until": kept.checked_until,
                "took": kept.took,
            }
        ).encode()
        self.store.set(
            key, header + b"\n" + raw, policy.lifetime.kept_for(kept.stale_until, now)
        )

    def forget(self, key: str) -> None:
        self.store.delete(key)

    def lead(self, key: str, policy: KeepPolicy[Any]) -> bool:
        return self.flight.lead(key, policy.wait_for_others * 5)

    def release(self, key: str) -> None:
        self.flight.release(key)

    def awaited(self, key: str, policy: KeepPolicy[Any]) -> _Kept | None:
        return awaited(lambda: self.read(key), policy.wait_for_others)


class Cache:
    def __init__(
        self,
        store: KeyValueStore | None = None,
        namespace: str = "",
        now: Callable[[], datetime] = utc_now,
        random: Callable[[], float] = random_module.random,
        enabled: bool = True,
    ) -> None:
        """`store` shares values across replicas — `None` keeps them as objects in this process;
        `namespace` keeps this cache's keys apart from others in the same store; `now` and
        `random` are the clock and the dice lifetimes are judged by — a `ManualClock.now` and a
        fixed roll in tests."""
        self.store = store
        self.namespace = namespace
        self.now = now
        self.random = random
        self.enabled = enabled
        self._process = _ProcessTier()

    def _key(self, key: Any, codec: Codec[Any] | None = None) -> str:
        """Context: on a shared store the codec's schema is part of the key — a deploy that
        changes the shape of a value reads a key of its own, never what an older one kept."""
        prefix = f"cache:{self.namespace}:" if self.namespace else "cache:"
        schema = "" if codec is None else codec.schema
        return f"{prefix}value:{key_of(key, schema)}"

    def _tier(self, codec: Codec[Any] | None) -> _ProcessTier | _StoreTier:
        if self.store is None:
            return self._process
        return _StoreTier(self.store, self._codec_required(codec))

    def _clock(self) -> float:
        return self.now().timestamp()

    def _still_valid[T](
        self,
        tier: _ProcessTier | _StoreTier,
        key: str,
        kept: _Kept,
        policy: KeepPolicy[T],
        now: float,
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

    def _computed[T](
        self,
        tier: _ProcessTier | _StoreTier,
        key: str,
        compute: Callable[[], T],
        policy: KeepPolicy[T],
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
            fresh_until = policy.lifetime.fresh_until(now, self.random())
            tier.write(
                key,
                _Kept(
                    value=value,
                    mark=mark,
                    fresh_until=fresh_until,
                    stale_until=policy.lifetime.stale_until(fresh_until),
                    checked_until=now + policy.validation.trusted_for.total_seconds(),
                    took=took,
                ),
                policy,
                now,
            )
            return value
        finally:
            tier.release(key)

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
        tier = self._tier(codec)
        hashed = self._key(key, codec if self.store is not None else None)
        kept = tier.read(hashed)
        now = self._clock()
        if kept is not None and now < kept.stale_until:
            if self._still_valid(tier, hashed, kept, policy, now):
                expiring = now >= kept.fresh_until or policy.lifetime.recompute_early(
                    kept.fresh_until, kept.took, now, self.random()
                )
                if not expiring:
                    return kept.value
                if tier.lead(hashed, policy):
                    return self._computed(tier, hashed, compute, policy)
                return kept.value
        if tier.lead(hashed, policy):
            return self._computed(tier, hashed, compute, policy)
        # What arrives must be what the leader kept: the invalid or expired value read above,
        # read again because nobody replaced it in time, is never served.
        arrived = tier.awaited(hashed, policy)
        if arrived is not None and arrived != kept:
            return arrived.value
        return compute()

    def forget(self, key: Any, codec: Codec[Any] | None = None) -> None:
        """Let go of the value kept under `key` — in this process, or on a shared store for every
        replica, where the key is found by the same codec it was kept with."""
        if self.store is None:
            self._process.forget(self._key(key))
            return
        _StoreTier(self.store, self._codec_required(codec)).forget(self._key(key, codec))

    def _codec_required(self, codec: Codec[Any] | None) -> Codec[Any]:
        if codec is None:
            raise ContractViolation(
                "a Cache on a shared store keeps bytes: pass the Codec its values are kept with"
            )
        return codec
