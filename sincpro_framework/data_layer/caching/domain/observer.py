"""What a cache or an idempotent run did with each call — for a span, a metric, a test.

Context: without these, fail-safe and a bypassed store hide incidents: the caller gets an answer
and nobody sees it was a stale one. Every outcome is reported, the observer decides what to record.
"""

from enum import StrEnum
from typing import Protocol


class CacheOutcome(StrEnum):
    """What a `Cache` or a `QueryCaching` call did — exactly one per call."""

    HIT = "hit"
    """A cached value, fresh and valid, served as is."""
    STALE = "stale"
    """A cached value past its lifetime, served while another caller recomputes it."""
    COMPUTED = "computed"
    """Computed by this caller — missing, expiring or no longer valid — and cached."""
    COALESCED = "coalesced"
    """Waited for another caller's computation and served its value."""
    INVALIDATED = "invalidated"
    """A cached value whose validation no longer holds: it was not served."""
    FALLBACK = "fallback"
    """Computing or validating failed and the last good value was served (fail-safe)."""
    BYPASSED = "bypassed"
    """The shared store failed or its breaker is open: the process tier stood in."""


class IdempotencyOutcome(StrEnum):
    """What an idempotent run did with its key — exactly one per call."""

    CLAIMED = "claimed"
    """This caller won the key and ran the write."""
    REPLAYED = "replayed"
    """A completed answer was served without running."""
    IN_PROGRESS = "in_progress"
    """The key is being run elsewhere and did not complete in time."""
    KEY_REUSED = "key_reused"
    """The key was used before for a different payload."""


class CacheObserver(Protocol):
    """The port: told every outcome, by namespace — a span, a metric, a test list."""

    def observed(self, outcome: str, namespace: str) -> None:
        """`outcome` is a `CacheOutcome` or an `IdempotencyOutcome`; `namespace` is the cache's
        or the idempotency's. Never raises into the call it observes."""
        ...
