"""`Problem`: one failure as every wire tells it — its kind, a stable reason, what the caller reads,
and **what to do next** — built once (`problem_of`) and drawn by each wire in its own encoding:
RFC 9457 on REST, an error's `data` on JSON-RPC, `google.rpc.Status` on gRPC, a tool error on MCP.

    problem_of(InvoiceNotFound("invoice F-9 does not exist"))
    ->  Problem(kind=not_found, reason="INVOICE_NOT_FOUND", title="Not found", status=404,
                detail="invoice F-9 does not exist", next_step=do_not_retry,
                guidance="What the request names does not exist: …", retryable=False)

Context: the defaults are the kind's — its title, its HTTP status, its next step and guidance
(`NEXT_STEP_OF`). An error changes what it means without any table to edit:

    class InvoiceNotFound(ClientError):
        failure_kind = FailureKind.NOT_FOUND                     what kind it is
        next_step = NextStep.FIX_REQUEST                         what the caller does
        guidance = "Check the invoice number on the printed copy."

        def refine_problem(self, problem: Problem) -> Problem:   anything else
            return problem.model_copy(update={"extensions": {"number": self.number}})

An error a project does not own (a library's) is translated into one of its own by an error
handler of the bus — the one place a project maps a failure. Nothing of a failure that is not
written for the caller (`said_to_the_caller`) is told: no detail, its kind as its reason.
"""

from collections.abc import Iterable, Mapping, Sequence
from datetime import timedelta
from enum import StrEnum
from typing import Any

from pydantic import ConfigDict, ValidationError

from sincpro_framework import exceptions
from sincpro_framework.common.failures.classification import (
    failure_reason,
    refined_failure_kind,
    retry_after,
    said_to_the_caller,
)
from sincpro_framework.sincpro_abstractions import DataTransferObject


class NextStep(StrEnum):
    """What a caller — a person, a client, an agent — does after a failure."""

    FIX_REQUEST = "fix_request"
    """Correct what was sent, then send it again."""
    AUTHENTICATE = "authenticate"
    """Get a credential, or a fresh one, then send it again."""
    ASK_FOR_ACCESS = "ask_for_access"
    """Do not retry: whoever calls may not do this until someone grants it."""
    DO_NOT_RETRY = "do_not_retry"
    """The same request fails the same way: show the answer and stop."""
    REFRESH_AND_RETRY = "refresh_and_retry"
    """Read what changed, then send the request again on what was read."""
    RETRY_LATER = "retry_later"
    """Send the same request again after `retry_after`."""
    VERIFY_THEN_RETRY = "verify_then_retry"
    """It may have run: check whether it did before sending it again — or send it again with the
    same idempotency key."""


NEXT_STEP_OF: Mapping[exceptions.FailureKind, tuple[NextStep, str]] = {
    exceptions.FailureKind.INVALID: (
        NextStep.FIX_REQUEST,
        "The request does not fit what the use case takes: correct the fields named and send "
        "it again.",
    ),
    exceptions.FailureKind.UNAUTHENTICATED: (
        NextStep.AUTHENTICATE,
        "Who is calling is not known: authenticate, or renew the credential, and send it again.",
    ),
    exceptions.FailureKind.PERMISSION_DENIED: (
        NextStep.ASK_FOR_ACCESS,
        "The caller may not do this: retrying will not help until the access is granted.",
    ),
    exceptions.FailureKind.NOT_FOUND: (
        NextStep.DO_NOT_RETRY,
        "What the request names does not exist: check the identifier; the same request "
        "fails the same way.",
    ),
    exceptions.FailureKind.CONFLICT: (
        NextStep.REFRESH_AND_RETRY,
        "Someone else wrote first: read the current state, then send the request again on it.",
    ),
    exceptions.FailureKind.IN_PROGRESS: (
        NextStep.RETRY_LATER,
        "The same request is still running: wait `retry_after`, then ask again for its answer.",
    ),
    exceptions.FailureKind.KEY_REUSED: (
        NextStep.FIX_REQUEST,
        "The idempotency key was used for another request: a new request needs a new key.",
    ),
    exceptions.FailureKind.DOMAIN: (
        NextStep.DO_NOT_RETRY,
        "A business rule refused the request: show the answer; the same request is refused "
        "the same way.",
    ),
    exceptions.FailureKind.EXHAUSTED: (
        NextStep.RETRY_LATER,
        "A limit was reached: wait `retry_after`, then send the same request again.",
    ),
    exceptions.FailureKind.UNAVAILABLE: (
        NextStep.RETRY_LATER,
        "Something the call needs is down for now: wait `retry_after`, then send the same "
        "request again.",
    ),
    exceptions.FailureKind.UNKNOWN_OUTCOME: (
        NextStep.VERIFY_THEN_RETRY,
        "The request was sent and no answer came back — it may have run: check before sending "
        "it again, or send it with the same idempotency key.",
    ),
    exceptions.FailureKind.INTERNAL: (
        NextStep.RETRY_LATER,
        "The service failed on its side: try again later, and report it with the trace if it "
        "keeps failing.",
    ),
}
"""What a caller does after each kind of failure, and why — the default an error may change."""

TITLE_OF: Mapping[exceptions.FailureKind, str] = {
    exceptions.FailureKind.INVALID: "The request does not validate",
    exceptions.FailureKind.UNAUTHENTICATED: "Who is calling is not known",
    exceptions.FailureKind.PERMISSION_DENIED: "Not allowed",
    exceptions.FailureKind.NOT_FOUND: "Not found",
    exceptions.FailureKind.CONFLICT: "The write collided",
    exceptions.FailureKind.IN_PROGRESS: "The same request is still running",
    exceptions.FailureKind.KEY_REUSED: "The idempotency key was used for another request",
    exceptions.FailureKind.DOMAIN: "The domain refused the request",
    exceptions.FailureKind.EXHAUSTED: "Too many requests",
    exceptions.FailureKind.UNAVAILABLE: "Unavailable for now",
    exceptions.FailureKind.UNKNOWN_OUTCOME: "It may have run — verify before retrying",
    exceptions.FailureKind.INTERNAL: "Internal error",
}

HTTP_STATUS_OF: Mapping[exceptions.FailureKind, int] = {
    exceptions.FailureKind.INVALID: 422,
    exceptions.FailureKind.UNAUTHENTICATED: 401,
    exceptions.FailureKind.PERMISSION_DENIED: 403,
    exceptions.FailureKind.NOT_FOUND: 404,
    exceptions.FailureKind.CONFLICT: 409,
    exceptions.FailureKind.IN_PROGRESS: 409,
    exceptions.FailureKind.KEY_REUSED: 422,
    exceptions.FailureKind.DOMAIN: 422,
    exceptions.FailureKind.EXHAUSTED: 429,
    exceptions.FailureKind.UNAVAILABLE: 503,
    exceptions.FailureKind.UNKNOWN_OUTCOME: 504,
    exceptions.FailureKind.INTERNAL: 500,
}
"""The HTTP status of each kind — the one REST answers, and any route of a project's own."""

RETRYABLE_STEPS = frozenset({NextStep.RETRY_LATER, NextStep.REFRESH_AND_RETRY})
"""The next steps that send the request again without changing it by hand."""


def pointer(location: Sequence[Any]) -> str:
    """A validation error's location as a JSON pointer into the DTO (RFC 6901) — where the value
    came from (`body`, `query`, …) dropped, `~` and `/` escaped.

        pointer(("body", "lines", 0, "amount"))  ->  "#/lines/0/amount"
    """
    parts = list(location)
    if parts and parts[0] in ("body", "query", "path", "header", "cookie"):
        parts = parts[1:]
    return "#/" + "/".join(str(one).replace("~", "~0").replace("/", "~1") for one in parts)


class FieldError(DataTransferObject):
    """One field the request got wrong — `pointer` addresses it in the DTO (`#/total`)."""

    pointer: str
    detail: str


class Problem(DataTransferObject):
    """One failure, as every wire tells it — as JSON, `retry_after` is a number of seconds, as
    the `Retry-After` header says it."""

    model_config = ConfigDict(ser_json_timedelta="float")

    kind: exceptions.FailureKind
    """What kind of failure — the same word on every wire."""
    reason: str
    """UPPER_SNAKE, stable: what a client switches on — never the text."""
    title: str
    status: int
    """Its HTTP status — what REST answers, and what a route of a project's own answers."""
    detail: str | None = None
    """What the caller reads of this failure — `None` when nothing of it is the caller's."""
    next_step: NextStep
    guidance: str
    """What to do next, in words — for a person or an agent reading it."""
    retryable: bool
    """Whether sending the request again, as it is or on a fresh read, may succeed."""
    retry_after: timedelta | None = None
    """How long to wait first — `None` when waiting does not help."""
    errors: list[FieldError] = []
    """The fields of the request that did not validate."""
    extensions: dict[str, Any] = {}
    """What the error chose to disclose besides — a missing permission, a step-up, a limit."""

    @classmethod
    def for_kind(
        cls,
        kind: exceptions.FailureKind,
        reason: str | None = None,
        detail: str | None = None,
        errors: Iterable[FieldError] = (),
        **extensions: Any,
    ) -> "Problem":
        """A problem of `kind` with its defaults — title, status, next step, guidance — and what
        is given; what an entrypoint answers for a failure no error raised (a body that does not
        parse, a route that does not exist).

            Problem.for_kind(FailureKind.INVALID, "UNREADABLE", "the body is not JSON")
        """
        step, guidance = NEXT_STEP_OF[kind]
        return cls(
            kind=kind,
            reason=reason or kind.value.upper(),
            title=TITLE_OF[kind],
            status=HTTP_STATUS_OF[kind],
            detail=detail,
            next_step=step,
            guidance=guidance,
            retryable=step in RETRYABLE_STEPS,
            errors=list(errors),
            extensions=extensions,
        )


def problem_of(error: Exception) -> Problem:
    """The problem every wire answers `error` with.

        problem_of(InvoiceNotFound("invoice F-9 does not exist"))
        ->  Problem(kind=not_found, reason="INVOICE_NOT_FOUND", status=404,
                    detail="invoice F-9 does not exist", next_step=do_not_retry, …)
        problem_of(RuntimeError("postgres://admin:pw@db"))
        ->  Problem(kind=internal, reason="INTERNAL", status=500, detail=None, …)

    1. The kind (`refined_failure_kind`) gives the defaults (`Problem.for_kind`); the reason is
       the error's class when the caller may read the error, its kind otherwise.
    2. What the caller reads is the error's message only when it was written for the caller.
    3. A request that did not validate names each field; a retryable kind says how long to wait.
    4. The error's `next_step` and `guidance`, when it declares them, take the kind's place.
    Final: the error's `refine_problem(problem)`, when it has one, changes anything else — what
    it answers is the problem when it is one.
    """
    kind = refined_failure_kind(error)
    errors = (
        [
            FieldError(pointer=pointer(one.get("loc", ())), detail=str(one.get("msg", "")))
            for one in error.errors(include_url=False)
        ]
        if isinstance(error, ValidationError)
        else []
    )
    problem = Problem.for_kind(
        kind, failure_reason(error, kind), said_to_the_caller(error), errors
    )
    step = getattr(error, "next_step", None)
    step = step if isinstance(step, NextStep) else problem.next_step
    guidance = getattr(error, "guidance", None)
    problem = problem.model_copy(
        update={
            "retry_after": retry_after(error, kind),
            "next_step": step,
            "retryable": step in RETRYABLE_STEPS,
            "guidance": guidance if isinstance(guidance, str) else problem.guidance,
        }
    )
    refine = getattr(error, "refine_problem", None)
    refined = refine(problem) if callable(refine) else problem
    return refined if isinstance(refined, Problem) else problem
