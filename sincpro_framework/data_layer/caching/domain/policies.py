"""What governs a cached value and a run-once write — the policies each call is judged by.

Context: frozen DTOs a composition builds once and hands every call. `CachePolicy` is what `Cache`
answers by — one strategy per question (`Freshness`, `Validation`, `FailurePolicy`) — `QueryCachePolicy`
what `QueryCaching` does (a Query's ttl, what it varies by, what it depends on),
`IdempotencyPolicy` what `Idempotency` does. A field one of them does not have is refused, never
ignored: `CachePolicy(ttl=...)` — a `QueryCachePolicy`'s field — would otherwise cache a value forever.
"""

from collections.abc import Sequence
from datetime import timedelta
from typing import Any

from pydantic import ConfigDict, Field, field_validator

from sincpro_framework.data_layer.caching.domain.failure import FailurePolicy, Raise
from sincpro_framework.data_layer.caching.domain.freshness import Freshness, TimeToLive
from sincpro_framework.data_layer.caching.domain.validation import Unconditional, Validation
from sincpro_framework.sincpro_abstractions import DataTransferObject


class CachePolicy[T](DataTransferObject):
    """How `Cache` judges one cached value — one strategy per question, plus two bounds.

        CachePolicy(
            freshness=TimeToLive(ttl=timedelta(minutes=5), stale_for=timedelta(minutes=1)),
            failure=FailSafe(serve_for=timedelta(hours=1)),
        )

    A field it does not have is refused, never ignored.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    freshness: Freshness = Field(default_factory=TimeToLive)
    """How long a cached value is served as is — `TimeToLive`, `Sliding`, or yours."""
    validation: Validation[T] = Field(default_factory=Unconditional)
    """Whether a cached value is still right, checked beside its freshness."""
    failure: FailurePolicy = Field(default_factory=Raise)
    """What a call answers when computing or validating fails — `Raise`, or `FailSafe`."""
    wait_for_others: timedelta = timedelta(seconds=2)
    """How long a caller that does not lead waits for the value the leader computes."""
    max_bytes: int = 1_048_576
    """A value larger than this, encoded, is answered and not stored in a shared store."""
    near: timedelta | None = None
    """How long a value on a shared store is also held in this process, decoded — read from
    here, its freshness and validation judged as always, without asking the store again; a
    value past `max_bytes` is held here too. Another replica's `forget` reaches this copy at
    most `near` later. `None`: every call asks the store."""


class QueryCachePolicy(DataTransferObject):
    """How `QueryCaching` caches one Query's answers — flat knobs, turned into a `TimeToLive`.

        QueryCachePolicy(ttl=timedelta(minutes=5), vary_by="tenant_id", depends_on=[Invoice])

    Built once by the composition and handed to `QueryCaching.on`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ttl: timedelta
    """How long an answer is served as is, before `jitter` spreads it."""
    vary_by: Sequence[str] = ()
    """Context keys each answer is cached per — a tenant, a locale; one alone or a list, stored as a
    tuple."""
    stale_for: timedelta = timedelta(0)
    """How long past its ttl an answer is still served while one caller recomputes it."""
    jitter: float = 0.1
    """The ttl is spread by up to this fraction, up or down."""
    early_expiry: float = 1.0
    """XFetch's β: how eagerly an answer about to expire is recomputed; 0 never early."""
    depends_on: Sequence[type] = ()
    """Aggregates the answer depends on beyond what the repository noted, as a list:
    `depends_on=[Invoice, Customer]`; stored as a tuple."""
    max_bytes: int = 1_048_576
    """An answer larger than this is answered and not cached."""
    wait_for_others: timedelta = timedelta(seconds=2)
    """How long a caller that did not win the lock waits for the answer the winner computes."""

    @field_validator("vary_by", "depends_on", mode="before")
    @classmethod
    def _one_or_several(cls, value: Any) -> Any:
        """A list or a tuple, stored as a tuple so the policy stays hashable; one key alone stands
        for a list of one: `vary_by="tenant_id"`."""
        return (value,) if isinstance(value, str) else tuple(value)

    @property
    def time_to_live(self) -> TimeToLive:
        """The flat knobs as the `TimeToLive` the answer's timeline is computed with.

            QueryCachePolicy(ttl=timedelta(minutes=5)).time_to_live
                ->  TimeToLive(ttl=5 min, jitter=0.1, stale_for=0, early_expiry=1.0)

        Built anew on every read — the policy itself stays flat.
        """
        return TimeToLive(
            ttl=self.ttl,
            jitter=self.jitter,
            stale_for=self.stale_for,
            early_expiry=self.early_expiry,
        )


class IdempotencyPolicy(DataTransferObject):
    """How `Idempotency` limits a write to once per key — how long, and per which context keys.

        IdempotencyPolicy(expires_after=timedelta(hours=24), vary_by="tenant_id")

    Built once by the composition and handed to every run.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    expires_after: timedelta
    """How long a completed answer is replayed — a transport retry, not a person repeating an
    action on purpose minutes later."""
    in_progress_for: timedelta = timedelta(minutes=1)
    """How long a claim holds without completing; must outlive the longest run."""
    wait_for_completion: timedelta = timedelta(0)
    """How long a duplicate arriving mid-run waits for the first answer before
    `AlreadyInProgress`."""
    vary_by: Sequence[str] = ()
    """Context keys the key is separated by — a tenant, a caller; one alone or a list. What
    identifies the request, the Command says itself: its `idempotency_key()` method."""

    @field_validator("vary_by", mode="before")
    @classmethod
    def _one_or_several(cls, value: Any) -> Any:
        """One context key alone stands for a list of one: `vary_by="tenant_id"`; stored as a
        tuple so the policy stays hashable."""
        return (value,) if isinstance(value, str) else tuple(value)
