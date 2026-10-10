"""What an auth refusal raises, whoever refused: two answers, one base.

Context: `ClientError`s — the caller is not known, or may not do this — so every entrypoint tells
the caller why — "you may not issue invoices"
is the answer, not the inside of the process. Two, not a taxonomy: a missing, invalid, expired or
revoked credential is `Unauthenticated` with its `reason`, because the caller does the same for
each — authenticate again. `PermissionDenied`, not `PermissionError`: Python's builtin is an
`OSError` about files, and a handler catching one would catch the other.
"""

from sincpro_framework.exceptions import ClientError, FailureKind


class AuthError(ClientError):
    """Catch this to catch any auth refusal."""

    reason: str
    """Why — what the caller is told, on every transport."""


class Unauthenticated(AuthError):
    """Who is calling is not known well enough. `step_up` names what the caller has to come back
    with — MFA, for RFC 9470's challenge."""

    failure_kind = FailureKind.UNAUTHENTICATED

    def __init__(self, reason: str, step_up: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.step_up = step_up


class PermissionDenied(AuthError):
    """A known identity may not do this — raised by the guard, or by a provider that wants to
    say why."""

    failure_kind = FailureKind.PERMISSION_DENIED

    def __init__(self, subject: str, requirement: str, reason: str = "") -> None:
        self.reason = reason or f"lacks {requirement}"
        super().__init__(f"{subject} may not: {self.reason}")
        self.subject = subject
        self.requirement = requirement
