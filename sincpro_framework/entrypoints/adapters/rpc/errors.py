"""The JSON-RPC error catalogue: one code per kind of failure, and what its `data` carries.

Context: a number alone is not a contract — the codes above -32000 mean other things in other
APIs — so every error's `data` carries the `kind` (`common.failures`, the same on every wire),
a stable `reason` (`failure_reason`'s, as on every wire: what a client switches on) and whether the
same request may succeed if sent again (`retryable`). The codes are PRD_15 §1.3's JSON-RPC
column, published once in the OpenRPC document's `components.errors`.
"""

import re
from collections.abc import Mapping
from typing import Any, NamedTuple

from pydantic import ValidationError

from sincpro_framework.auth.domain.exceptions import AuthError
from sincpro_framework.auth.entrypoint.transports import refusal_body
from sincpro_framework.common.failures import (
    failure_reason,
    json_safe_validation_errors,
    refined_failure_kind,
    said_to_the_caller,
)
from sincpro_framework.ddd.exceptions import DuplicateAggregate
from sincpro_framework.exceptions import FailureKind

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
UNAVAILABLE = -32000
UNAUTHENTICATED = -32001
"""Who is calling is not known well enough — the HTTP host answers a lone one with 401."""
PERMISSION_DENIED = -32003
NOT_FOUND = -32004
CONFLICT = -32009
"""A write collided — a newer version of the aggregate, or a duplicate."""
DOMAIN_ERROR = -32010
"""The domain refused the request; `data.message` is its message."""
KEY_REUSED = -32022
UNKNOWN_OUTCOME = -32024
"""The call was sent and no answer came back — it may have run; verify before sending it again."""
TRY_LATER = -32029
"""An idempotent run of the same key is in flight, or a limit is exhausted — `data.kind` says
which."""


class ErrorCode(NamedTuple):
    name: str
    """The key under OpenRPC `components.errors`."""
    code: int
    message: str
    kinds: tuple[str, ...]
    retryable: bool
    """The usual answer; `data.retryable` of one error is the one to trust."""


PROTOCOL_ERRORS = (
    ErrorCode("ParseError", PARSE_ERROR, "Parse error", ("invalid",), False),
    ErrorCode("InvalidRequest", INVALID_REQUEST, "Invalid Request", ("invalid",), False),
    ErrorCode("MethodNotFound", METHOD_NOT_FOUND, "Method not found", ("not_found",), False),
)
"""Answered before any method is known — so no method's `errors` lists them."""

METHOD_ERRORS = (
    ErrorCode("InvalidParams", INVALID_PARAMS, "Invalid params", ("invalid",), False),
    ErrorCode(
        "Unauthenticated", UNAUTHENTICATED, "Unauthenticated", ("unauthenticated",), False
    ),
    ErrorCode(
        "PermissionDenied",
        PERMISSION_DENIED,
        "Permission denied",
        ("permission_denied",),
        False,
    ),
    ErrorCode("NotFound", NOT_FOUND, "Not found", ("not_found",), False),
    ErrorCode("Conflict", CONFLICT, "Conflict", ("conflict",), True),
    ErrorCode("TryLater", TRY_LATER, "Try again later", ("in_progress", "exhausted"), True),
    ErrorCode("KeyReused", KEY_REUSED, "Idempotency key reused", ("key_reused",), False),
    ErrorCode("DomainError", DOMAIN_ERROR, "Domain error", ("domain",), False),
    ErrorCode("Unavailable", UNAVAILABLE, "Unavailable", ("unavailable",), True),
    ErrorCode(
        "UnknownOutcome", UNKNOWN_OUTCOME, "Unknown outcome", ("unknown_outcome",), False
    ),
    ErrorCode("InternalError", INTERNAL_ERROR, "Internal error", ("internal",), True),
)
"""What a method may answer — PRD_15 §1.3, the JSON-RPC column."""

BY_KIND = {kind: one for one in METHOD_ERRORS for kind in one.kinds}
"""Looked up by the kind's value, so a kind `common.failures` learns later (`not_found`,
`unavailable`, `exhausted`) is answered with its code without a change here."""

RETRYABLE_BY_KIND = {kind: one.retryable for kind, one in BY_KIND.items()}
NOT_RETRYABLE = (DuplicateAggregate,)
"""A duplicate stays a duplicate: sending the same write again collides again — unlike a stale
write, which a fresh run re-reads."""

UPPER_SNAKE_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

type Answer = tuple[int, str, dict[str, Any]]
"""The code, message and `data` one error is answered with."""


def upper_snake(name: str) -> str:
    return UPPER_SNAKE_BOUNDARY.sub("_", name).upper()


def error_data(kind: str, reason: str, retryable: bool, **disclosed: Any) -> dict[str, Any]:
    return {"kind": kind, "reason": reason, "retryable": retryable, **disclosed}


def protocol_error(code: ErrorCode, reason: str, **disclosed: Any) -> Answer:
    """A refusal of the request itself — parse, invalid request, unknown method, a limit."""
    return (
        code.code,
        code.message,
        error_data(code.kinds[0], reason, code.retryable, **disclosed),
    )


def answer_for(error: Exception) -> Answer:
    """The code, message and `data` a failure of a method is answered with.

    1. A `ValidationError` is invalid params, with the validation errors.
    2. An auth refusal keeps what the caller comes back with (`step_up`, `requirement`), its
       text as `message`.
    3. Any other failure is answered by its kind; a kind with no code here is internal.
    4. Final: the reason is `failure_reason`'s, the one every wire answers with — the error's
       class only when the caller may read the error (a `DomainError`), the kind otherwise,
       so an `HsmDown` that is `unavailable` says `UNAVAILABLE` here as on REST, gRPC and a
       queue. The message of a `DomainError` is disclosed; nothing of any other failure is,
       and an internal one's reason is `INTERNAL_ERROR`.
    """
    if isinstance(error, ValidationError):
        code = BY_KIND[FailureKind.INVALID.value]
        data = error_data(
            "invalid",
            "VALIDATION_ERROR",
            False,
            errors=json_safe_validation_errors(error),
        )
        return code.code, code.message, data
    kind = refined_failure_kind(error).value
    code = BY_KIND.get(kind, BY_KIND[FailureKind.INTERNAL.value])
    if code.code == INTERNAL_ERROR:
        return code.code, code.message, error_data("internal", "INTERNAL_ERROR", True)
    retryable = RETRYABLE_BY_KIND[kind] and not isinstance(error, NOT_RETRYABLE)
    reason = failure_reason(error, FailureKind(kind))
    if isinstance(error, AuthError):
        refusal = {**refusal_body(error), "message": error.reason}
        return code.code, code.message, {**refusal, **error_data(kind, reason, retryable)}
    disclosed = said_to_the_caller(error)
    extra = {"message": disclosed} if disclosed is not None else {}
    return code.code, code.message, error_data(kind, reason, retryable, **extra)


def error_components() -> dict[str, dict[str, Any]]:
    """OpenRPC `components.errors`: every code once, its `data` naming the kinds it carries."""
    return {
        one.name: {
            "code": one.code,
            "message": one.message,
            "data": {"kind": list(one.kinds), "retryable": one.retryable},
        }
        for one in (*PROTOCOL_ERRORS, *METHOD_ERRORS)
    }


def method_error_refs() -> list[dict[str, str]]:
    return [{"$ref": f"#/components/errors/{one.name}"} for one in METHOD_ERRORS]


ERROR_DATA_SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "description": "What every error's `data` carries, whatever else it adds "
    "(`message`, `errors`, `method`, `requirement`, `step_up`, `limit`).",
    "required": ["kind", "reason", "retryable"],
    "properties": {
        "kind": {
            "type": "string",
            "enum": sorted(
                {kind for one in (*PROTOCOL_ERRORS, *METHOD_ERRORS) for kind in one.kinds}
            ),
        },
        "reason": {"type": "string", "pattern": "^[A-Z][A-Z0-9_]*$"},
        "retryable": {"type": "boolean"},
    },
}
