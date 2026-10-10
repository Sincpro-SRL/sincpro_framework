"""What kind of failure an error is — the vocabulary every error declares and every wire answers
with its own code."""

from enum import StrEnum


class FailureKind(StrEnum):
    """What a failure is, whatever the wire — each wire answers it with its own code, so a
    client of any of them tells the same things apart."""

    INVALID = "invalid"
    """The request did not validate as the DTO."""
    UNAUTHENTICATED = "unauthenticated"
    PERMISSION_DENIED = "permission_denied"
    NOT_FOUND = "not_found"
    """What the request names does not exist — declared by the error (`failure_kind = ...`)."""
    CONFLICT = "conflict"
    """A write collided: a newer version of the aggregate, or a duplicate of a unique value."""
    IN_PROGRESS = "in_progress"
    """The same idempotency key is running elsewhere right now — retry once it completes."""
    KEY_REUSED = "key_reused"
    """An idempotency key came back with another payload — a new request needs a new key."""
    DOMAIN = "domain"
    """The domain refused the request — the answer to it, told to the caller."""
    EXHAUSTED = "exhausted"
    """A quota or a rate was spent — retry later. Declared by the error."""
    UNAVAILABLE = "unavailable"
    """Something this call needs is down for now — retry. Declared by the error."""
    UNKNOWN_OUTCOME = "unknown_outcome"
    """The call was sent and no answer came back: it may have run. Verify — or retry with an
    idempotency key — before doing it again or undoing it. Declared by the error."""
    INTERNAL = "internal"
    """The inside of the process failed — nothing of it is told."""
