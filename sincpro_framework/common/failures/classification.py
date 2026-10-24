"""What a driving adapter is allowed to tell a caller about a failure.

Shared by every wire (JSON-RPC, gRPC, REST, queues): the status code differs per protocol, the
classification, the stable reason and the disclosure policy do not (PRD_15 §1.3).

    class InvoiceNotFound(ClientError):
        failure_kind = FailureKind.NOT_FOUND        # an error declares a kind of its own

Context: `refined_failure_kind` is the classification every wire (REST, JSON-RPC, gRPC, FastAPI,
queues) and the `ExecutionFailed` event answer with — each wire's table has a row for every kind.
The error says its own kind: every base of `sincpro_framework.exceptions` declares one, so nothing
here knows the components that raise them.
"""

import json
import re
from datetime import timedelta
from typing import Any

from pydantic import ValidationError

from sincpro_framework import exceptions

RETRYABLE = frozenset(
    {
        exceptions.FailureKind.IN_PROGRESS,
        exceptions.FailureKind.EXHAUSTED,
        exceptions.FailureKind.UNAVAILABLE,
    }
)
"""The kinds a caller retries as they are, after a delay (`retry_after`)."""

PERMANENT = frozenset(
    {
        exceptions.FailureKind.INVALID,
        exceptions.FailureKind.UNAUTHENTICATED,
        exceptions.FailureKind.PERMISSION_DENIED,
        exceptions.FailureKind.NOT_FOUND,
        exceptions.FailureKind.KEY_REUSED,
        exceptions.FailureKind.DOMAIN,
    }
)
"""The kinds that fail the same way on every attempt: a queue consumer dead-letters them, the
event relay gives up on them at the first failure — trying again only blocks what comes after."""

DEFAULT_RETRY_AFTER = timedelta(seconds=1)
UPPER_SNAKE_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def refined_failure_kind(error: Exception) -> exceptions.FailureKind:
    """The kind every wire answers a failure with: what the error declares (`failure_kind` on its
    class — every base of `sincpro_framework.exceptions` declares one), a request that did not
    validate as its DTO, and a crash for anything else."""
    declared = getattr(error, "failure_kind", None)
    if isinstance(declared, str):
        try:
            return exceptions.FailureKind(declared)
        except ValueError:
            pass
    if isinstance(error, ValidationError):
        return exceptions.FailureKind.INVALID
    return exceptions.FailureKind.INTERNAL


def said_to_the_caller(error: Exception) -> str | None:
    """What of a failure a client is allowed to read.

    **A `ClientError` or a `DomainError` was written for whoever asked** — "an invoice has to balance" is the
    answer, and hiding it helps nobody. Anything else is the inside of the process, and its
    message routinely carries what must never leave it: a connection string with a password, a
    statement with the value it was filtering on, a path on the server.

        raise OperationalError("SELECT … WHERE token = 'secret'", …, "postgres://admin:hunter2@…")

    That whole string used to reach the client as the error's `data`. The log still gets all of
    it — the host's `logger.exception` runs either way — which is where it belongs.
    """
    caused_inside = (exceptions.ProgrammingError, exceptions.ExternalServiceError)
    return (
        str(error)
        if isinstance(error, exceptions.FrameworkError)
        and not isinstance(error, caused_inside)
        else None
    )


def is_fault(
    error: BaseException,
    ignored: tuple[type[BaseException], ...] = (),
    reported: tuple[type[BaseException], ...] = (),
) -> bool:
    """Whether a failure is a problem of the service — a bug, a service down, an outcome nobody
    knows — or an answer the caller was meant to get: what something not found, refused or
    invalid is. The one rule every signal follows, so none contradicts another:

        fault       GlitchTip, an `error` log line with its traceback, a span in error,
                    `sincpro.fault="true"` on the metrics, `fault=True` on `ExecutionFailed`
        not fault   none of that: an `info` line, `sincpro.fault="false"`

    1. What a bus asked to see (`reported`) is a fault, whatever it is.
    2. What a bus said is traffic (`ignored`) is not.
    Final: a `ClientError` or a `DomainError` — written for whoever asked — is not; anything else
    is: a crash, a `ProgrammingError`, an `ExternalServiceError`.
    """
    if isinstance(error, reported):
        return True
    if isinstance(error, ignored):
        return False
    return not (isinstance(error, Exception) and said_to_the_caller(error) is not None)


def failure_reason(error: Exception, kind: exceptions.FailureKind) -> str:
    """The stable, UPPER_SNAKE reason a client switches on: the error's class when the caller may
    read the error (`InvoiceNotFound` → `INVOICE_NOT_FOUND`), the kind otherwise — the class of
    an inside failure (`OperationalError`) says what runs inside, which is not the caller's.
    """
    if kind == exceptions.FailureKind.INVALID or said_to_the_caller(error) is None:
        return kind.value.upper()
    return UPPER_SNAKE_BOUNDARY.sub("_", type(error).__name__).upper()


def retry_after(error: Exception, kind: exceptions.FailureKind) -> timedelta | None:
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
