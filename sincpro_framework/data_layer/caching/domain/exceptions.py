"""What an idempotent run refuses, in its own words.

Context: `DomainError`s — expected traffic an entrypoint answers (409 for a run in flight, 422 for
a key reused with another payload, as the IETF `Idempotency-Key` draft does), never a failure to
report.
"""

from sincpro_framework.ddd.exceptions import DomainError


class IdempotencyError(DomainError):
    """Catch this to catch any idempotency refusal."""


class AlreadyInProgress(IdempotencyError):
    """The same key is being run elsewhere right now — try again once it completes."""


class KeyReused(IdempotencyError):
    """The key was used before for a different payload — a new request needs a new key."""
