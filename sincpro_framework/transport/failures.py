"""What a driving adapter is allowed to tell a caller about a failure.

Shared by every wire (JSON-RPC, gRPC, REST, queues): the status code differs per protocol, the
classification, the stable reason and the disclosure policy do not (PRD_15 §1.3).

    class InvoiceNotFound(DomainError):
        failure_kind = FailureKind.NOT_FOUND        # an error declares a kind of its own

Context: `refined_failure_kind` is the classification every wire (REST, JSON-RPC, gRPC, FastAPI,
queues) and the `ExecutionFailed` event answer with — each wire's table has a row for every kind.
`failure_kind` is its shared core: the kinds a failure has whatever it declares (`INVALID`,
`UNAUTHENTICATED`, `PERMISSION_DENIED`, `CONFLICT`, `DOMAIN`, `INTERNAL`).
"""

import json
import re
from datetime import timedelta
from enum import StrEnum
from typing import Any

from pydantic import ValidationError


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


RETRYABLE = frozenset(
    {FailureKind.IN_PROGRESS, FailureKind.EXHAUSTED, FailureKind.UNAVAILABLE}
)
"""The kinds a caller retries as they are, after a delay (`retry_after`)."""

DEFAULT_RETRY_AFTER = timedelta(seconds=1)
UPPER_SNAKE_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def failure_kind(error: Exception) -> FailureKind:
    from sincpro_framework.auth.domain import PermissionDenied, Unauthenticated
    from sincpro_framework.ddd.exceptions import (
        DomainError,
        DuplicateAggregate,
        StaleAggregate,
    )

    if isinstance(error, ValidationError):
        return FailureKind.INVALID
    if isinstance(error, Unauthenticated):
        return FailureKind.UNAUTHENTICATED
    if isinstance(error, PermissionDenied):
        return FailureKind.PERMISSION_DENIED
    if isinstance(error, (StaleAggregate, DuplicateAggregate)):
        return FailureKind.CONFLICT
    if isinstance(error, DomainError):
        return FailureKind.DOMAIN
    return FailureKind.INTERNAL


def _declared_kind(error: Exception) -> FailureKind | None:
    declared = getattr(type(error), "failure_kind", None)
    try:
        return FailureKind(declared) if isinstance(declared, str) else None
    except ValueError:
        return None


def refined_failure_kind(error: Exception) -> FailureKind:
    """The kind every wire answers a failure with: what the error declares
    (`failure_kind = FailureKind.NOT_FOUND` on its class), then the idempotency refusals, then
    the shared kinds (`failure_kind`)."""
    from sincpro_framework.caching.domain.exceptions import AlreadyInProgress, KeyReused

    declared = _declared_kind(error)
    if declared is not None:
        return declared
    if isinstance(error, AlreadyInProgress):
        return FailureKind.IN_PROGRESS
    if isinstance(error, KeyReused):
        return FailureKind.KEY_REUSED
    return failure_kind(error)


def failure_reason(error: Exception, kind: FailureKind) -> str:
    """The stable, UPPER_SNAKE reason a client switches on: the error's class when the caller may
    read the error (`InvoiceNotFound` → `INVOICE_NOT_FOUND`), the kind otherwise — the class of
    an inside failure (`OperationalError`) says what runs inside, which is not the caller's.
    """
    if kind == FailureKind.INVALID or said_to_the_caller(error) is None:
        return kind.value.upper()
    return UPPER_SNAKE_BOUNDARY.sub("_", type(error).__name__).upper()


def retry_after(error: Exception, kind: FailureKind) -> timedelta | None:
    """How long a caller waits before retrying — `None` when retrying as it is will not help.
    The error may say (`retry_after`, seconds or a `timedelta`); a second otherwise."""
    if kind not in RETRYABLE:
        return None
    declared = getattr(error, "retry_after", None)
    if isinstance(declared, timedelta):
        return declared
    if (
        isinstance(declared, (int, float))
        and not isinstance(declared, bool)
        and declared >= 0
    ):
        return timedelta(seconds=declared)
    return DEFAULT_RETRY_AFTER


def json_safe_validation_errors(error: ValidationError) -> list[dict[str, Any]]:
    """Pydantic puts the raw exception in ctx.error for value_error types (any
    ValueObject validate_fn that rejects input). json.dumps on that raises, so the
    error envelope would crash the host instead of returning an invalid-params status.
    """
    return json.loads(json.dumps(error.errors(), default=str))


def said_to_the_caller(error: Exception) -> str | None:
    """What of a failure a client is allowed to read.

    **A `DomainError` was written for whoever asked** — "an invoice has to balance" is the
    answer, and hiding it helps nobody. Anything else is the inside of the process, and its
    message routinely carries what must never leave it: a connection string with a password, a
    statement with the value it was filtering on, a path on the server.

        raise OperationalError("SELECT … WHERE token = 'secret'", …, "postgres://admin:hunter2@…")

    That whole string used to reach the client as the error's `data`. The log still gets all of
    it — the host's `logger.exception` runs either way — which is where it belongs.
    """
    from sincpro_framework.ddd.exceptions import DomainError

    return str(error) if isinstance(error, DomainError) else None
