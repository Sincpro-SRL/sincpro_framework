"""What an auth refusal raises, whoever refused: two answers, one base.

Context: `ClientError`s — the caller is not known, or may not do this — so every entrypoint tells
the caller why — "you may not issue invoices"
is the answer, not the inside of the process. Two, not a taxonomy: a missing, invalid, expired or
revoked credential is `Unauthenticated` with its `reason`, because the caller does the same for
each — authenticate again. `PermissionDenied`, not `PermissionError`: Python's builtin is an
`OSError` about files, and a handler catching one would catch the other.
"""

from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from sincpro_framework.common.failures.problem import Problem
from sincpro_framework.exceptions import (
    ClientError,
    ExternalServiceError,
    FailureKind,
    ProgrammingError,
    ServiceUnavailableError,
)


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
    with — MFA, for RFC 9470's challenge; `anonymous` says it presented nothing at all."""

    failure_kind = FailureKind.UNAUTHENTICATED
    http_status = 401

    def __init__(
        self,
        reason: str,
        step_up: str | None = None,
        max_age: int | None = None,
        anonymous: bool = False,
    ) -> None:
        """A refusal saying why — `"expired token"` — and, for a step-up, what to bring and,
        in seconds, how recent the authentication must be. `anonymous` when the caller
        presented no credential: its challenge names no error (RFC 6750 §3.1) — there is no
        token to call invalid."""
        super().__init__(reason)
        self.reason = reason
        self.step_up = step_up
        self.max_age = max_age
        self.anonymous = anonymous

    def refusal_body(self) -> dict[str, Any]:
        """The kind and the reason, `step_up` and `max_age` when named, and `anonymous` when
        the caller presented nothing."""
        body = super().refusal_body()
        if self.step_up:
            body["step_up"] = self.step_up
        if self.max_age is not None:
            body["max_age"] = self.max_age
        if self.anonymous:
            body["anonymous"] = True
        return body


def refusal_from(said: Any) -> Unauthenticated | None:
    """The `Unauthenticated` a wire's answer describes — a REST problem body, a JSON-RPC
    error's `data`, a `refusal_body()` — rebuilt where only the answer is left, to choose the
    challenge; `None` for an answer of any other kind.

        refusal_from({"kind": "unauthenticated", "detail": "mfa required", "step_up": "mfa"})
            ->  Unauthenticated("mfa required", step_up="mfa")
        refusal_from({"kind": "permission_denied", ...})       ->  None

    Context: the reason is the problem's `detail`, the JSON-RPC `message`, or a
    `refusal_body`'s own `reason` — in the first two `reason` is a code, never the text;
    `step_up`, `max_age` and `anonymous` sit beside it, as `refusal_body` wrote them.
    """
    if not isinstance(said, Mapping) or said.get("kind") != FailureKind.UNAUTHENTICATED.value:
        return None
    reason = said.get("detail", said.get("message", said.get("reason", "")))
    step_up, max_age = said.get("step_up"), said.get("max_age")
    return Unauthenticated(
        reason if isinstance(reason, str) else "",
        step_up if isinstance(step_up, str) else None,
        max_age if isinstance(max_age, int) and not isinstance(max_age, bool) else None,
        anonymous=said.get("anonymous") is True,
    )


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


class EndpointUnavailable(ServiceUnavailableError):
    """An endpoint auth depends on did not answer usefully — an identity provider's discovery,
    its keys, its token or introspection endpoint, a policy engine: unreachable, past its time,
    a server error, or an answer that cannot be read.

    Context: never `Unauthenticated` — the caller's credentials did not fail, the service could
    not check them — so every wire answers 503, with `Retry-After` when the endpoint asked for
    one. `endpoint` is said without its query, userinfo or fragment: it may be logged.

        EndpointUnavailable("https://auth.acme.bo/realms/a/protocol/openid-connect/certs",
                            "answered 503", status=503, retry_after=timedelta(seconds=30))
    """

    def __init__(
        self,
        endpoint: str,
        reason: str,
        status: int | None = None,
        retry_after: timedelta | None = None,
    ) -> None:
        """A failure of `endpoint`, saying why; `status` when it answered one."""
        super().__init__(f"{endpoint} {reason}")
        self.endpoint = endpoint
        self.reason = reason
        self.status = status
        self.retry_after = retry_after


class InvalidGrant(ClientError):
    """A grant that cannot be given — a role the catalog does not declare, two roles never held
    together, two levels of one privilege — said to whoever administers it (400)."""


class ConcurrentChange(ClientError):
    """What an administrator changed was changed by another at the same moment, again and again
    — read it again and decide over what is there now (409)."""

    failure_kind = FailureKind.CONFLICT


class CredentialsRejected(ExternalServiceError):
    """An authorization server refused what this service presented to get a token — its client
    (`invalid_client`), its scope, its grant: this service's configuration, never the caller's
    credentials, so never `Unauthenticated` (the caller would be told to log in again, and
    loop). Running it again as it is fails the same way.

        CredentialsRejected("https://auth.bank.bo/token", "invalid_client", "unknown client")
    """

    def __init__(
        self,
        endpoint: str,
        error: str,
        description: str = "",
        dpop_nonce: str | None = None,
    ) -> None:
        """What `endpoint` answered: its `error` code, cut short its `error_description`, and
        the `DPoP-Nonce` it demands proofs carry from now on, when it sent one (RFC 9449 §8).
        """
        clean = "".join(one for one in description if one.isprintable())[:200]
        super().__init__(f"{endpoint} refused: {error}" + (f" — {clean}" if clean else ""))
        self.endpoint = endpoint
        self.error = error
        self.description = clean
        self.dpop_nonce = dpop_nonce


class GrantRejected(CredentialsRejected):
    """A refresh token or a device code the server no longer honours (`invalid_grant`): the
    grant is dropped, and consent must be given again."""


class NoCredentialsFor(ClientError):
    """The tenant this call acts for has no credentials for the API it asked — never answered
    with another tenant's (403)."""

    failure_kind = FailureKind.PERMISSION_DENIED

    def __init__(self, tenant: str | None, source: str) -> None:
        """`tenant`, the one the call acts for; `source`, the credentials it has none of."""
        super().__init__(f"tenant {tenant!r} has no credentials for {source}")
        self.tenant = tenant
        self.source = source


class PasswordRejected(ClientError):
    """A password that cannot be taken as it came — longer than a hasher accepts (400)."""
