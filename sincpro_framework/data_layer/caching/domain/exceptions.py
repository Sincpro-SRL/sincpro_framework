"""What an idempotent run refuses, in its own words.

Context: `ClientError`s — expected traffic an entrypoint answers (409 for a run in flight, 422 for
a key reused with another payload, as the IETF `Idempotency-Key` draft does), never a failure to
report.
"""

from sincpro_framework.exceptions import ClientError, FailureKind


class AlreadyInProgress(ClientError):
    """The same key is being run elsewhere right now — try again once it completes."""

    failure_kind = FailureKind.IN_PROGRESS


class KeyReused(ClientError):
    """The key was used before for a different payload — a new request needs a new key."""

    failure_kind = FailureKind.KEY_REUSED
