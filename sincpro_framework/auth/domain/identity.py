"""What a caller presents, and who they turn out to be.

    Credentials(transport="http", headers={"authorization": "Bearer eyJ…"})    not verified
        → AuthProvider.authenticate →
    Identity(subject="user:42", tenant="bo", permissions={"billing.invoice.issue"})

Context: DTOs, because both cross layers and are built from what nobody trusts until validated;
an `Identity` also travels — the credential one service sends another carries it. An `Identity`
is never `None`: a caller nobody vouched for is `Identity.anonymous()`.
"""

from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Any

from pydantic import ConfigDict, Field, field_validator

from sincpro_framework.sincpro_abstractions import DataTransferObject


class IdentityKind(StrEnum):
    USER = "user"
    SERVICE = "service"
    SYSTEM = "system"
    ANONYMOUS = "anonymous"


class Credentials(DataTransferObject):
    """What arrived with the call, normalized by the entrypoint whatever the transport — every
    field an `AuthProvider` may need to verify it, and nothing it has to parse out of a request.
    """

    model_config = ConfigDict(frozen=True)

    transport: str
    """`http`, `grpc`, `mcp`, `queue` — where the call came from."""
    headers: Mapping[str, str] = Field(default_factory=dict)
    """Lower-case names: HTTP headers, gRPC metadata, a message's headers."""
    cookies: Mapping[str, str] = Field(default_factory=dict)
    """A session cookie — Odoo's `session_id`."""
    method: str | None = None
    uri: str | None = None
    """The HTTP method and target, for a proof bound to the request — DPoP (RFC 9449), HTTP
    message signatures."""
    body: bytes | None = None
    """The raw body, for an HMAC signature over it — a webhook, SigV4. Read only when a provider
    says `needs_body`."""
    peer_certificate: bytes | None = None
    """The client certificate of an mTLS connection, DER — SPIFFE, a service mesh."""

    @field_validator("headers")
    @classmethod
    def _lower_case(cls, headers: Mapping[str, str]) -> Mapping[str, str]:
        return {name.lower(): value for name, value in headers.items()}

    @property
    def bearer(self) -> str | None:
        """The token of `Authorization: Bearer <token>`, when that is the header."""
        scheme, _, token = self.headers.get("authorization", "").partition(" ")
        return token.strip() or None if scheme.lower() == "bearer" else None

    def __repr__(self) -> str:
        """Context: never the values — a log line that printed credentials would leak them."""
        return f"Credentials(transport={self.transport!r}, headers={sorted(self.headers)})"

    __str__ = __repr__


class Identity(DataTransferObject):
    model_config = ConfigDict(frozen=True)

    subject: str
    """`user:42`, `service:billing`, `apikey:ops-bot` — what logs and spans show."""
    kind: IdentityKind
    tenant: str | None = None
    """The company or database the call acts for."""
    permissions: frozenset[str] = frozenset()
    """What the identity may do, as the provider resolved it — from the token, from roles, from
    an API. A `Permission` member is a `str`."""
    roles: frozenset[str] = frozenset()
    """The roles the provider found, for a provider or a screen that asks; the guard reads
    `permissions` and asks the provider."""
    provider: str | None = None
    """The name of the `AuthProvider` that vouched for it — the one asked about its permissions.
    `None` for one opened by hand (`as_identity`): its `permissions` are the answer."""
    actor: "Identity | None" = None
    """The service acting on the subject's behalf, when one is."""
    claims: Mapping[str, Any] = Field(default_factory=dict)
    """What the credential said besides, read-only; typed with `claims_as`."""
    reason: str | None = None
    """Why the system acts, for a SYSTEM identity — on its log line."""

    @property
    def is_anonymous(self) -> bool:
        return self.kind == IdentityKind.ANONYMOUS

    def claims_as[T: DataTransferObject](self, shape: type[T]) -> T:
        return shape.model_validate(dict(self.claims))

    @classmethod
    def user(
        cls,
        subject: str,
        tenant: str | None = None,
        permissions: Iterable[str] = (),
        roles: Iterable[str] = (),
    ) -> "Identity":
        return cls(
            subject=subject,
            kind=IdentityKind.USER,
            tenant=tenant,
            permissions=frozenset(permissions),
            roles=frozenset(roles),
        )

    @classmethod
    def service(cls, name: str, permissions: Iterable[str] = ()) -> "Identity":
        return cls(
            subject=f"service:{name}",
            kind=IdentityKind.SERVICE,
            permissions=frozenset(permissions),
        )

    @classmethod
    def system(cls, reason: str) -> "Identity":
        return cls(subject="system", kind=IdentityKind.SYSTEM, reason=reason)

    @classmethod
    def anonymous(cls) -> "Identity":
        return cls(subject="anonymous", kind=IdentityKind.ANONYMOUS)


ANONYMOUS = Identity.anonymous()
