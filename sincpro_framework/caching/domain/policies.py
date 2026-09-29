"""What governs a kept value and a run-once write — the policies each call is judged by.

Context: frozen DTOs a composition builds once and hands every call. `KeepPolicy` is what `Cache`
answers by — one strategy per question (`Freshness`, `Validation`, `FailurePolicy`) — `CachePolicy`
what `QueryCaching` does (a Query's ttl, what it varies by, what it depends on),
`IdempotencyPolicy` what `Idempotency` does.
"""

from datetime import timedelta
from typing import Any

from pydantic import ConfigDict, Field, model_validator

from sincpro_framework.caching.domain.failure import FailurePolicy, Raise
from sincpro_framework.caching.domain.freshness import Freshness, TimeToLive
from sincpro_framework.caching.domain.validation import Unconditional, Validation
from sincpro_framework.sincpro_abstractions import DataTransferObject


class KeepPolicy[T](DataTransferObject):
    model_config = ConfigDict(frozen=True)

    freshness: Freshness = Field(default_factory=TimeToLive)
    """How long a kept value is served as is — `TimeToLive`, `Sliding`, or yours."""
    validation: Validation[T] = Field(default_factory=Unconditional)
    """Whether a kept value is still right, checked beside its freshness."""
    failure: FailurePolicy = Field(default_factory=Raise)
    """What a call answers when computing or validating fails — `Raise`, or `FailSafe`."""
    wait_for_others: timedelta = timedelta(seconds=2)
    """How long a caller that does not lead waits for the value the leader computes."""
    max_bytes: int = 1_048_576
    """A value larger than this, encoded, is answered and not kept in a shared store."""

    @model_validator(mode="before")
    @classmethod
    def _lifetime_is_freshness(cls, data: Any) -> Any:
        """Context: phase 1 named the field `lifetime`; `KeepPolicy(lifetime=...)` still works."""
        if isinstance(data, dict) and "lifetime" in data and "freshness" not in data:
            data = {**data, "freshness": data["lifetime"]}
            del data["lifetime"]
        return data

    @property
    def lifetime(self) -> Freshness:
        return self.freshness


class CachePolicy(DataTransferObject):
    model_config = ConfigDict(frozen=True)

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

    @property
    def lifetime(self) -> TimeToLive:
        return TimeToLive(
            ttl=self.ttl,
            jitter=self.jitter,
            stale_for=self.stale_for,
            early_expiry=self.early_expiry,
        )


class IdempotencyPolicy(DataTransferObject):
    model_config = ConfigDict(frozen=True)

    expires_after: timedelta
    """How long a completed answer is replayed — a transport retry, not a person repeating an
    action on purpose minutes later."""
    in_progress_for: timedelta = timedelta(minutes=1)
    """How long a claim holds without completing; must outlive the longest run."""
    wait_for_completion: timedelta = timedelta(0)
    """How long a duplicate arriving mid-run waits for the first answer before
    `AlreadyInProgress`."""
    vary_by: tuple[str, ...] = ()
    """Context keys the key is kept per — a tenant, a caller. Which fields of the Command make
    the key, the Command says itself: `Annotated[str, IdempotencyKey]`."""
