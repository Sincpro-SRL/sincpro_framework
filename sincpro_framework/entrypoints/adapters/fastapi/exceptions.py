"""What the FastAPI gateway raises."""

from sincpro_framework.auth.domain.exceptions import (
    AuthError,
    PermissionDenied,
    Unauthenticated,
)
from sincpro_framework.ddd.exceptions import DuplicateAggregate, StaleAggregate
from sincpro_framework.entrypoints.adapters.rest.exceptions import InvalidRequest
from sincpro_framework.exceptions import ClientError, FailureKind, ProgrammingError


class IdempotencyKeyMissing(ClientError):
    """A use case that runs once, whose Command says nothing of what identifies it, called
    without an `Idempotency-Key` header — answered 400: the retry could not be told from a new
    request."""
