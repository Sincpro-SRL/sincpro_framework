"""What an auth refusal raises, whoever refused: two answers, one base.

Context: `ClientError`s — the caller is not known, or may not do this — so every entrypoint tells
the caller why — "you may not issue invoices"
is the answer, not the inside of the process. Two, not a taxonomy: a missing, invalid, expired or
revoked credential is `Unauthenticated` with its `reason`, because the caller does the same for
each — authenticate again. `PermissionDenied`, not `PermissionError`: Python's builtin is an
`OSError` about files, and a handler catching one would catch the other.
"""

from typing import Any

from sincpro_framework.common.failures.problem import Problem
from sincpro_framework.exceptions import ClientError, FailureKind, ProgrammingError


class AuthError(ClientError):
    """Catch this to catch any auth refusal."""

    reason: str
    """Why — what the caller is told, on every transport."""
    http_status: int
    """The HTTP answer to the refusal — 401 asks the caller to authenticate again, 403 does
    not. A gRPC or JSON-RPC wire answers its own code for `failure_kind` instead."""

    def refusal_body(self) -> dict[str, Any]:
        """What a caller is told of a refusal — the reason, what was missing, what to come back
        with — the same fields on every transport.

            Unauthenticated("expired token").refusal_body()
                ->  {"kind": "unauthenticated", "reason": "expired token"}
            Unauthenticated("mfa required", step_up="mfa").refusal_body()
                ->  {"kind": "unauthenticated", "reason": "mfa required", "step_up": "mfa"}
            PermissionDenied("user:1", "billing.invoice.issue").refusal_body()
                ->  {"kind": "permission_denied", "reason": "lacks billing.invoice.issue",
                     "requirement": "billing.invoice.issue"}

        Context: the kind and the reason here; each refusal adds what it alone knows —
        `step_up` only when one was named, `requirement` only for a denied permission.
        """
        return {"kind": self.failure_kind.value, "reason": self.reason}

    def refine_problem(self, problem: Problem) -> Problem:
        """The problem of a refusal: its reason as what the caller reads, its own HTTP status, and
        what else the refusal says — a `step_up`, a missing `requirement` — as extensions."""
        said = {
            name: value
            for name, value in self.refusal_body().items()
            if name not in ("kind", "reason")
        }
        return problem.model_copy(
            update={"detail": self.reason, "status": self.http_status, "extensions": said}
        )


class Unauthenticated(AuthError):
    """Who is calling is not known well enough. `step_up` names what the caller has to come back
    with — MFA, for RFC 9470's challenge."""

    failure_kind = FailureKind.UNAUTHENTICATED
    http_status = 401

    def __init__(self, reason: str, step_up: str | None = None) -> None:
        """A refusal saying why — `"expired token"` — and, for a step-up, what to bring."""
        super().__init__(reason)
        self.reason = reason
        self.step_up = step_up

    def refusal_body(self) -> dict[str, Any]:
        """The kind and the reason, and `step_up` when one was named."""
        body = super().refusal_body()
        if self.step_up:
            body["step_up"] = self.step_up
        return body


class PermissionDenied(AuthError):
    """A known identity may not do this — raised by the guard, or by a provider that wants to
    say why."""

    failure_kind = FailureKind.PERMISSION_DENIED
    http_status = 403

    def __init__(self, subject: str, requirement: str, reason: str = "") -> None:
        """A refusal of `subject` for `requirement` — by default `lacks <requirement>`.

        str(PermissionDenied("user:1", "billing.invoice.issue"))
            ->  "user:1 may not: lacks billing.invoice.issue"
        """
        self.reason = reason or f"lacks {requirement}"
        super().__init__(f"{subject} may not: {self.reason}")
        self.subject = subject
        self.requirement = requirement

    def refusal_body(self) -> dict[str, Any]:
        """The kind and the reason, and the `requirement` that was missing."""
        return {**super().refusal_body(), "requirement": self.requirement}
