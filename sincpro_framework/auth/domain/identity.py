"""What a caller presents, and who they turn out to be.

    Credentials(transport="http", headers={"authorization": "Bearer eyJ…"})    not verified
        → AuthProvider.authenticate →
    Identity(subject="user:42", tenant="bo", permissions={"billing.invoice.issue"})

Context: DTOs, because both cross layers and are built from what nobody trusts until validated;
an `Identity` also travels — the credential one service sends another carries it. An `Identity`
is never `None`: a caller nobody vouched for is `Identity.anonymous()`.
"""

from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import parse_qsl

from pydantic import ConfigDict, Field, field_validator

from sincpro_framework.sincpro_abstractions import DataTransferObject


class IdentityKind(StrEnum):
    """What kind of caller an `Identity` is — a person, a peer service, the system acting on
    its own (which every requirement admits), or nobody."""

    USER = "user"
    SERVICE = "service"
    SYSTEM = "system"
    ANONYMOUS = "anonymous"


def _first(values: Iterable[tuple[str, str]]) -> dict[str, str]:
    """Name/value pairs as a dict holding the first value of a repeated name.

        _first([("a", "1"), ("b", "2"), ("a", "3")])   ->  {"a": "1", "b": "2"}

    Context: a credential sent twice is ambiguous; the first one wins, the same on every
    transport, rather than whichever a dict comprehension would retain — the last.
    """
    found: dict[str, str] = {}
    for name, value in values:
        found.setdefault(name, value)
    return found


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
    query: Mapping[str, str] = Field(default_factory=dict)
    """The query string, first value per name — a token a legacy client sends as
    `?access_token=`."""
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
        """Header names lower-cased, so a provider looks one up without caring how it came."""
        return {name.lower(): value for name, value in headers.items()}

    @classmethod
    def from_headers(
        cls,
        transport: str,
        headers: Iterable[tuple[str, Any]] | Mapping[str, Any],
        peer_certificate: bytes | None = None,
    ) -> "Credentials":
        """Credentials out of a transport's headers or metadata — text values only; a binary
        gRPC entry (`-bin`) is never a credential. A `cookie` header is read into `cookies`.

            Credentials.from_headers("grpc", [("Authorization", "Bearer eyJ…"),
                                              ("trace-bin", b"…"),
                                              ("Cookie", "session_id=ab")])
                ->  Credentials(transport="grpc",
                                headers={"authorization": "Bearer eyJ…",
                                         "cookie": "session_id=ab"},
                                cookies={"session_id": "ab"})

        Context: names are lower-cased and the first value of a repeated name wins.
        """
        pairs = headers.items() if isinstance(headers, Mapping) else headers
        text = _first(
            (str(name).lower(), value)
            for name, value in pairs
            if isinstance(value, str) and not str(name).lower().endswith("-bin")
        )
        cookie = SimpleCookie()
        if "cookie" in text:
            cookie.load(text["cookie"])
        return cls(
            transport=transport,
            headers=text,
            cookies={name: morsel.value for name, morsel in cookie.items()},
            peer_certificate=peer_certificate,
        )

    @classmethod
    def from_asgi(cls, scope: Mapping[str, Any], transport: str = "http") -> "Credentials":
        """Credentials out of an ASGI scope — headers, cookies, query, method and target; the
        client certificate when the server hands the TLS extension.

            Credentials.from_asgi({"method": "GET", "path": "/invoices",
                                   "query_string": b"access_token=abc&page=2",
                                   "headers": [(b"x-api-key", b"sk_3f9")]})
                ->  Credentials(transport="http", headers={"x-api-key": "sk_3f9"},
                                query={"access_token": "abc", "page": "2"},
                                method="GET", uri="/invoices?access_token=abc&page=2")

        Context: the first certificate of `client_cert_chain`, encoded, is the peer's; the
        `uri` is the path alone when there is no query string.
        """
        headers = [
            (name.decode("latin-1"), value.decode("latin-1"))
            for name, value in scope["headers"]
        ]
        raw_query = scope.get("query_string", b"").decode("latin-1")
        path = scope.get("path", "")
        tls = scope.get("extensions", {}).get("tls", {})
        chain: Sequence[str] = tls.get("client_cert_chain") or ()
        base = cls.from_headers(transport, headers, chain[0].encode() if chain else None)
        return base.model_copy(
            update={
                "query": _first(parse_qsl(raw_query, keep_blank_values=True)),
                "method": scope.get("method"),
                "uri": f"{path}?{raw_query}" if raw_query else path,
            }
        )

    @property
    def bearer(self) -> str | None:
        """The token of `Authorization: Bearer <token>`, when that is the header."""
        scheme, _, token = self.headers.get("authorization", "").partition(" ")
        return token.strip() or None if scheme.lower() == "bearer" else None

    def __repr__(self) -> str:
        """The transport and the header names only.

        Context: never the values — a log line that printed credentials would leak them.

            Credentials(transport="http", headers={"Authorization": "Bearer x"})
                ->  "Credentials(transport='http', headers=['authorization'])"
        """
        return f"Credentials(transport={self.transport!r}, headers={sorted(self.headers)})"

    __str__ = __repr__


class Identity(DataTransferObject):
    """Who a call acts as, once a provider vouched for it — or as opened by hand.

    Identity.user("user:42", tenant="bo", permissions={"billing.invoice.issue"})
    Identity.service("billing")         ->  subject "service:billing"
    Identity.system("nightly close")    ->  admitted by every requirement, reason logged
    Identity.anonymous()                ->  nobody — what is in play outside `as_identity`
    """

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
        """Whether nobody is calling."""
        return self.kind == IdentityKind.ANONYMOUS

    def claims_as[T: DataTransferObject](self, shape: type[T]) -> T:
        """The raw `claims`, validated into a DTO of the project's — typed access to what the
        credential said besides.

            class OdooClaims(DataTransferObject):
                uid: int
                company_ids: list[int]

            identity.claims_as(OdooClaims).company_ids   ->  [1, 3]
        """
        return shape.model_validate(dict(self.claims))

    @classmethod
    def user(
        cls,
        subject: str,
        tenant: str | None = None,
        permissions: Iterable[str] = (),
        roles: Iterable[str] = (),
    ) -> "Identity":
        """A person, opened by hand — a test, in-process code; its `permissions` are the
        answer, as no provider vouched for it."""
        return cls(
            subject=subject,
            kind=IdentityKind.USER,
            tenant=tenant,
            permissions=frozenset(permissions),
            roles=frozenset(roles),
        )

    @classmethod
    def service(cls, name: str, permissions: Iterable[str] = ()) -> "Identity":
        """A peer service, subject `service:<name>` — also the `actor` of a service token."""
        return cls(
            subject=f"service:{name}",
            kind=IdentityKind.SERVICE,
            permissions=frozenset(permissions),
        )

    @classmethod
    def system(cls, reason: str) -> "Identity":
        """The system acting on its own, for `reason` — prefer `as_system`, which logs it."""
        return cls(subject="system", kind=IdentityKind.SYSTEM, reason=reason)

    @classmethod
    def anonymous(cls) -> "Identity":
        """Nobody — what a call no provider recognized acts as."""
        return cls(subject="anonymous", kind=IdentityKind.ANONYMOUS)


ANONYMOUS = Identity.anonymous()
