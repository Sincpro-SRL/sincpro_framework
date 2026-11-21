"""Step-up (RFC 9470): a use case may require how strongly, and how recently, its caller
authenticated — refused with 401 and what to come back with, never 403; skipped when said; the
system passes; and `describe()` says it."""

from datetime import UTC, datetime, timedelta

import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.auth import (
    AccessControl,
    Assurance,
    Identity,
    Permission,
    Unauthenticated,
    WhenDenied,
    security,
)
from sincpro_framework.auth.server import Issuer, JwtProvider, SharedSecrets

NOW = datetime(2026, 11, 20, 12, tzinfo=UTC)


class P(Permission):
    APPROVE = "billing.payment.approve"


class CommandApprove(DataTransferObject):
    amount: int = 1


class CommandPreview(DataTransferObject):
    amount: int = 1


class Answer(DataTransferObject):
    by: str = ""


def guarded() -> tuple[UseFramework, AccessControl[P]]:
    auth = AccessControl[P](clock=lambda: NOW.timestamp())
    billing = UseFramework("step-up-billing", log_after_execution=False)

    @billing.feature(CommandApprove)
    @auth.requires(P.APPROVE, auth.assurance(methods={"mfa"}, max_age=timedelta(minutes=5)))
    class Approve(Feature):
        def execute(self, dto: CommandApprove) -> Answer:
            return Answer(by=security.identity.subject)

    @billing.feature(CommandPreview)
    @auth.requires(auth.assurance(acr=("gold",)), when_denied=WhenDenied.SKIP)
    class Preview(Feature):
        def execute(self, dto: CommandPreview) -> Answer:
            return Answer(by="preview")

    auth.on(billing)
    return billing, auth


def person(methods: set[str], minutes_ago: int, level: str | None = None) -> Identity:
    return Identity.user("user:ana", permissions={P.APPROVE}).model_copy(
        update={
            "assurance": Assurance(level=level, methods=frozenset(methods)),
            "authenticated_at": NOW - timedelta(minutes=minutes_ago),
        }
    )


def test_a_strong_recent_authentication_is_let_through() -> None:
    billing, _ = guarded()
    with security.as_identity(person({"pwd", "mfa"}, minutes_ago=2)):
        assert billing(CommandApprove(), Answer).by == "user:ana"


@pytest.mark.parametrize(
    ("identity", "reason"),
    [
        (person({"pwd"}, minutes_ago=2), "mfa required"),
        (person({"pwd", "mfa"}, minutes_ago=30), "a recent authentication required"),
        (Identity.user("user:ana", permissions={P.APPROVE}), "mfa required"),
    ],
)
def test_too_weak_or_too_old_is_asked_to_step_up_never_forbidden(
    identity: Identity, reason: str
) -> None:
    billing, _ = guarded()
    with security.as_identity(identity), pytest.raises(
        Unauthenticated, match=reason
    ) as refused:
        billing(CommandApprove(), Answer)
    assert refused.value.step_up == "mfa" and refused.value.max_age == 300
    assert refused.value.refusal_body() == {
        "kind": "unauthenticated",
        "reason": refused.value.reason,
        "step_up": "mfa",
        "max_age": 300,
    }


def test_a_skipped_step_up_answers_none_and_the_system_passes() -> None:
    billing, _ = guarded()
    with security.as_identity(person({"pwd"}, minutes_ago=1, level="silver")):
        assert billing(CommandPreview(), Answer) is None
    with security.as_system("nightly approvals"):
        assert billing(CommandApprove(), Answer) is not None


def test_describe_says_the_assurance() -> None:
    _, auth = guarded()
    described = auth.describe().use_cases
    assert described[f"{__name__}.CommandApprove"] == (
        "billing.payment.approve and assurance(mfa, 5m)"
    )
    assert described[f"{__name__}.CommandPreview"] == "assurance(gold) (else skipped)"


def test_the_challenge_asks_for_what_to_come_back_with() -> None:
    jwt = JwtProvider(
        Issuer("billing", audience="b", keys=SharedSecrets({"k": "x" * 32})), realm="billing"
    )
    refusal = Unauthenticated("mfa required", step_up="mfa", max_age=300)
    assert jwt.challenge_for(refusal) == (
        'Bearer realm="billing", error="insufficient_user_authentication", '
        'error_description="mfa required", acr_values="mfa", max_age=300'
    )
