"""What a transport hands auth, and what auth hands back — the one place every entrypoint goes
through, so JSON-RPC, gRPC, MCP, an ASGI app of the project's and a call to another service all
authenticate the same way.

    credentials = credentials_from_asgi(scope)             # or credentials_from_headers(...)
    with authenticated_as(bus, credentials):               # the bus's AccessControl, if any
        bus(command)

Context: standard library only — a transport's own library (grpc, fastmcp, starlette) stays in
its entrypoint, so a project that installs none of them imports this all the same. A bus nobody
guards is left alone: no `AccessControl`, no authentication, the identity as it was found.
"""

from collections.abc import Generator, Iterable, Mapping, Sequence
from contextlib import contextmanager, nullcontext
from http.cookies import SimpleCookie
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl

from sincpro_framework.auth.access_control import access_control_of, access_controls_for
from sincpro_framework.auth.domain import (
    AuthError,
    Credentials,
    Identity,
    PermissionDenied,
    Unauthenticated,
)
from sincpro_framework.auth.security_context import as_identity, current_identity

if TYPE_CHECKING:
    from sincpro_framework.use_bus import UseFramework

HTTP_STATUS = {Unauthenticated: 401, PermissionDenied: 403}
"""The HTTP answer to each refusal — 401 asks the caller to authenticate again, 403 does not."""


def http_status_of(error: AuthError) -> int:
    return next(status for kind, status in HTTP_STATUS.items() if isinstance(error, kind))


def refusal_body(error: AuthError) -> dict[str, Any]:
    """What a caller is told of a refusal — the reason, what was missing, what to come back
    with — the same fields on every transport."""
    body: dict[str, Any] = {
        "kind": (
            "unauthenticated" if isinstance(error, Unauthenticated) else "permission_denied"
        ),
        "reason": error.reason,
    }
    if isinstance(error, Unauthenticated) and error.step_up:
        body["step_up"] = error.step_up
    if isinstance(error, PermissionDenied):
        body["requirement"] = error.requirement
    return body


def _first(values: Iterable[tuple[str, str]]) -> dict[str, str]:
    found: dict[str, str] = {}
    for name, value in values:
        found.setdefault(name, value)
    return found


def credentials_from_headers(
    transport: str,
    headers: Iterable[tuple[str, Any]] | Mapping[str, Any],
    peer_certificate: bytes | None = None,
) -> Credentials:
    """Credentials out of a transport's headers or metadata — text values only; a binary
    gRPC entry (`-bin`) is never a credential. A `cookie` header is read into `cookies`."""
    pairs = headers.items() if isinstance(headers, Mapping) else headers
    text = _first(
        (str(name).lower(), value)
        for name, value in pairs
        if isinstance(value, str) and not str(name).lower().endswith("-bin")
    )
    cookie = SimpleCookie()
    if "cookie" in text:
        cookie.load(text["cookie"])
    return Credentials(
        transport=transport,
        headers=text,
        cookies={name: morsel.value for name, morsel in cookie.items()},
        peer_certificate=peer_certificate,
    )


def credentials_from_asgi(scope: Mapping[str, Any], transport: str = "http") -> Credentials:
    """Credentials out of an ASGI scope — headers, cookies, query, method and target; the
    client certificate when the server hands the TLS extension."""
    headers = [
        (name.decode("latin-1"), value.decode("latin-1")) for name, value in scope["headers"]
    ]
    raw_query = scope.get("query_string", b"").decode("latin-1")
    path = scope.get("path", "")
    tls = scope.get("extensions", {}).get("tls", {})
    chain: Sequence[str] = tls.get("client_cert_chain") or ()
    base = credentials_from_headers(transport, headers, chain[0].encode() if chain else None)
    return base.model_copy(
        update={
            "query": _first(parse_qsl(raw_query, keep_blank_values=True)),
            "method": scope.get("method"),
            "uri": f"{path}?{raw_query}" if raw_query else path,
        }
    )


@contextmanager
def authenticated_as(
    bus: "UseFramework", credentials: Credentials | None
) -> Generator[Identity, None, None]:
    """Act, inside the block, as whoever `credentials` say is calling `bus` — by the
    `AccessControl` guarding it. No credentials, or a bus nobody guards: the identity in play
    stays. `Unauthenticated` when the credentials are a provider's and fail."""
    access = access_control_of(bus)
    if credentials is None or access is None:
        with nullcontext(current_identity()) as unchanged:
            yield unchanged
        return
    with as_identity(access.authenticate(credentials)) as identity:
        yield identity


def challenges_of(buses: Iterable["UseFramework"]) -> list[str]:
    """Every `WWW-Authenticate` value the buses' providers answer a 401 with, once each."""
    found: list[str] = []
    for bus in buses:
        access = access_control_of(bus)
        for challenge in access.challenges() if access else ():
            if challenge not in found:
                found.append(challenge)
    return found


def identity_headers(context: str) -> dict[str, str]:
    """The headers a call to the service hosting `context` carries, so it knows who the call
    acts for — what the identity's provider issues (`credentials_for`); empty when nothing is
    guarded, nobody is calling, or no provider issues any.

    Context: only what a provider issues travels — never the identity's claims, which may hold
    what must not leave the process (a tenant's own credential)."""
    identity = current_identity()
    if identity.is_anonymous:
        return {}
    for access in access_controls_for(context):
        issued = access.credentials_for(identity)
        if issued is not None:
            return dict(issued.headers)
    return {}
