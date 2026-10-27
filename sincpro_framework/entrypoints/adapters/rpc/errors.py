"""The JSON-RPC error catalogue: one code per kind of failure, and what its `data` carries.

Context: a number alone is not a contract — the codes above -32000 mean other things in other
APIs — so every error's `data` carries the `Problem` every wire tells (`common.failures.problem_of`):
the `kind`, a stable `reason` a client switches on, whether the same request may succeed if sent
again (`retryable`), and what to do next (`next_step`, `guidance`, `retry_after` in seconds). The
codes are PRD_15 §1.3's JSON-RPC column, published once in the OpenRPC document's
`components.errors`.
"""

from collections.abc import Mapping
from typing import Any, NamedTuple

from pydantic import ValidationError

from sincpro_framework.common import failures
from sincpro_framework.entrypoints.adapters.rpc import exceptions

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
    """One row of the catalogue: a JSON-RPC code, the failure kinds it answers and whether a
    retry usually helps — published once under OpenRPC `components.errors`.

        ErrorCode("NotFound", -32004, "Not found", ("not_found",), False)
    """

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


type Answer = tuple[int, str, dict[str, Any]]
"""The code, message and `data` one error is answered with."""


def error_data(kind: str, reason: str, retryable: bool, **disclosed: Any) -> dict[str, Any]:
    """An error's `data`: the members every error carries, then what it discloses — the next step
    and its guidance are the kind's (`common.failures.NEXT_STEP_OF`) unless given.

        error_data("invalid", "BATCH_TOO_LARGE", False, limit=50)
        ->  {"kind": "invalid", "reason": "BATCH_TOO_LARGE", "retryable": False,
             "next_step": "fix_request", "guidance": "…", "limit": 50}

    The extra keywords are what the error chose to disclose.
    """
    step, guidance = failures.NEXT_STEP_OF[exceptions.FailureKind(kind)]
    return {
        "kind": kind,
        "reason": reason,
        "retryable": retryable,
        "next_step": step.value,
        "guidance": guidance,
        **disclosed,
    }


def protocol_error(code: ErrorCode, reason: str, **disclosed: Any) -> Answer:
    """A refusal of the request itself — parse, invalid request, unknown method, a limit."""
    return (
        code.code,
        code.message,
        error_data(code.kinds[0], reason, code.retryable, **disclosed),
    )


def answer_for(error: Exception) -> Answer:
    """The code, message and `data` a failure of a method is answered with.

        answer_for(InvoiceNotFound("F-9 does not exist"))
        ->  (-32004, "Not found", {"kind": "not_found", "reason": "INVOICE_NOT_FOUND",
                                   "retryable": False, "next_step": "do_not_retry",
                                   "guidance": "…", "message": "F-9 does not exist"})
        answer_for(RuntimeError("postgres://admin:pw@db"))
        ->  (-32603, "Internal error", {"kind": "internal", "reason": "INTERNAL_ERROR",
                                        "retryable": True, "next_step": "retry_later", …})

    1. A `ValidationError` is invalid params, with the validation errors.
    2. Any other failure is its `Problem` (`common.failures.problem_of`): its kind picks the
       code — a kind with no code here is internal — and `data` carries what the problem says:
       the next step, the guidance, how long to wait, what the caller reads (`message`) and what
       the error disclosed (an auth refusal's `step_up` or `requirement`).
    Final: an internal failure's reason is `INTERNAL_ERROR`, and nothing of it is told.
    """
    if isinstance(error, ValidationError):
        code = BY_KIND[exceptions.FailureKind.INVALID.value]
        data = error_data(
            "invalid",
            "VALIDATION_ERROR",
            False,
            errors=failures.json_safe_validation_errors(error),
        )
        return code.code, code.message, data
    problem = failures.problem_of(error)
    code = BY_KIND.get(problem.kind.value, BY_KIND[exceptions.FailureKind.INTERNAL.value])
    told = problem.model_dump(
        mode="json", include={"next_step", "guidance", "retry_after"}, exclude_none=True
    )
    if code.code == INTERNAL_ERROR:
        return (
            code.code,
            code.message,
            error_data("internal", "INTERNAL_ERROR", problem.retryable, **told),
        )
    if problem.detail is not None:
        told["message"] = problem.detail
    data = error_data(
        problem.kind.value, problem.reason, problem.retryable, **told, **problem.extensions
    )
    return code.code, code.message, data


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
    """A method's OpenRPC `errors`: a reference to each code a method may answer — the
    protocol errors are left out, they come before any method is known.

        method_error_refs()  ->  [{"$ref": "#/components/errors/InvalidParams"}, ...]
    """
    return [{"$ref": f"#/components/errors/{one.name}"} for one in METHOD_ERRORS]


ERROR_DATA_SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "description": "What every error's `data` carries, whatever else it adds "
    "(`retry_after`, `message`, `errors`, `method`, `requirement`, `step_up`, `limit`).",
    "required": ["kind", "reason", "retryable", "next_step", "guidance"],
    "properties": {
        "kind": {
            "type": "string",
            "enum": sorted(
                {kind for one in (*PROTOCOL_ERRORS, *METHOD_ERRORS) for kind in one.kinds}
            ),
        },
        "reason": {"type": "string", "pattern": "^[A-Z][A-Z0-9_]*$"},
        "retryable": {"type": "boolean"},
        "next_step": {"type": "string", "enum": [one.value for one in failures.NextStep]},
        "guidance": {"type": "string"},
        "retry_after": {"type": "number", "description": "Seconds to wait first."},
    },
}
