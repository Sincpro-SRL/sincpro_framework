"""What happened to the accounts a service manages itself — sessions begun and ended, a refresh
token replayed, an account locked, a one-time link sent and used — published when the piece
that saw it was given a publisher.

    issuer = TokenIssuer("https://billing", "billing", keys, refresh_tokens, revocations,
                         publisher=audit)
    issuer.issue(ana)   ->  SignedIn(entity_id="user:ana", session="sid_…", methods=["pwd"])

Context: a token is never in an event, nor what a one-time link carries — a session is named by
its id, a link by its purpose. `RefreshTokenReused` is an incident: the token was replayed past
its grace, so it was stolen or leaked, and its whole session was revoked.
"""

from dataclasses import dataclass, field

from sincpro_framework.auth.common.domain.events import AuthEvent
from sincpro_framework.ddd.events import DeliverableEventMixin


@dataclass(kw_only=True)
class SignedIn(DeliverableEventMixin, AuthEvent):
    """A session begun: the tokens `TokenIssuer.issue` handed out."""

    name = "auth.session.v1.signed_in"
    label: dict[str, str] = field(
        default_factory=lambda: {"default": "Signed in", "es": "Inició sesión"}
    )
    tenant: str | None = None
    session: str = ""
    client: str | None = None
    methods: list[str] = field(default_factory=list)
    """How the subject authenticated (`amr`): `pwd`, `otp`, `mfa`…"""
    scopes: list[str] = field(default_factory=list)


@dataclass(kw_only=True)
class SignedOut(DeliverableEventMixin, AuthEvent):
    """A session ended by a logout — its tokens refused from now on."""

    name = "auth.session.v1.signed_out"
    label: dict[str, str] = field(
        default_factory=lambda: {"default": "Signed out", "es": "Cerró sesión"}
    )
    session: str = ""


@dataclass(kw_only=True)
class SignedOutEverywhere(DeliverableEventMixin, AuthEvent):
    """Every session of a subject ended — a password changed, an account disabled."""

    name = "auth.session.v1.signed_out_everywhere"
    label: dict[str, str] = field(
        default_factory=lambda: {
            "default": "Signed out everywhere",
            "es": "Se cerraron todas sus sesiones",
        }
    )


@dataclass(kw_only=True)
class RefreshTokenReused(DeliverableEventMixin, AuthEvent):
    """A refresh token used again past its grace — replayed: its session was revoked."""

    name = "auth.session.v1.refresh_token_reused"
    label: dict[str, str] = field(
        default_factory=lambda: {
            "default": "Refresh token reused — session revoked",
            "es": "Se reutilizó un token — sesión revocada",
        }
    )
    session: str = ""


@dataclass(kw_only=True)
class AccountLocked(DeliverableEventMixin, AuthEvent):
    """Too many failed attempts: `entity_id` — the username or key counted — locked a while."""

    name = "auth.account.v1.locked"
    label: dict[str, str] = field(
        default_factory=lambda: {"default": "Account locked", "es": "Se bloqueó la cuenta"}
    )
    locked_seconds: int = 0


@dataclass(kw_only=True)
class OneTimeTokenIssued(DeliverableEventMixin, AuthEvent):
    """A one-time link issued to a subject for a purpose — a password reset, an invitation."""

    name = "auth.one_time_token.v1.issued"
    label: dict[str, str] = field(
        default_factory=lambda: {"default": "One-time link sent", "es": "Se envió un enlace"}
    )
    purpose: str = ""


@dataclass(kw_only=True)
class OneTimeTokenRedeemed(DeliverableEventMixin, AuthEvent):
    """A one-time link used — once."""

    name = "auth.one_time_token.v1.redeemed"
    label: dict[str, str] = field(
        default_factory=lambda: {"default": "One-time link used", "es": "Se usó un enlace"}
    )
    purpose: str = ""
