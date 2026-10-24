"""Auth on the MCP wire: each tool call acts as whoever called it, and — when asked — FastMCP
verifies bearer tokens with the bus's `AccessControl`.

    build_mcp_server(billing)                                  # a guarded bus: each call authenticated
    build_mcp_server(billing, auth=auth, base_url="https://…")  # plus FastMCP's own bearer check

Context: FastMCP is imported only inside these functions, so the module loads without the
`[mcp]` extra. A tool call reads, in order: the identity a `token_verifier` already put in the
access token, then the HTTP request's credentials — a bearer, an `x-api-key`, a query token —
authenticated by the bus's `AccessControl`; over stdio there is no request, and the identity is
the one the process opened.
"""

import asyncio
from collections.abc import Generator
from contextlib import contextmanager, nullcontext
from typing import TYPE_CHECKING, Any

from sincpro_framework.auth.domain.identity import Credentials, Identity
from sincpro_framework.auth.entrypoint.transports import (
    authenticated_as,
    credentials_from_asgi,
)
from sincpro_framework.auth.infrastructure.security_context import (
    as_identity,
    current_identity,
)
from sincpro_framework.entrypoints.adapters.mcp import exceptions
from sincpro_framework.entrypoints.adapters.mcp.mcp import FASTMCP_MISSING

if TYPE_CHECKING:
    from sincpro_framework.auth.entrypoint.access_control import AccessControl
    from sincpro_framework.use_bus import UseFramework

IDENTITY_CLAIM = "sincpro_identity"
"""Where a verified identity rides inside FastMCP's access token — in the process, never sent."""


def _call_state() -> tuple[Identity | None, Credentials | None]:
    """What the current tool call says about its caller — a verified identity, or the
    credentials to authenticate; `(None, None)` when it says nothing.

        bearer verified by `token_verifier`   ->  (Identity(subject="ana", ...), None)
        HTTP call, no verifier               ->  (None, Credentials(transport="mcp", ...))
        stdio, or FastMCP not installed      ->  (None, None)

    1. No FastMCP: nothing to read.
    2. An access token carrying `IDENTITY_CLAIM`: the identity FastMCP's verifier already
       authenticated — not authenticated twice.
    3. No HTTP request in flight (stdio): nothing to read.
    Final: the request's credentials, read from its ASGI scope.
    """
    try:
        from fastmcp.server.dependencies import (  # pyright: ignore[reportMissingImports]
            get_access_token,
            get_http_request,
        )
    except ImportError:
        return None, None
    token = get_access_token()
    if token is not None and IDENTITY_CLAIM in (token.claims or {}):
        return Identity.model_validate(token.claims[IDENTITY_CLAIM]), None
    try:
        request = get_http_request()
    except RuntimeError:
        return None, None
    return None, credentials_from_asgi(request.scope, "mcp")


@contextmanager
def acting_for_tool_call(bus: "UseFramework | None") -> Generator[Identity, None, None]:
    """The identity one tool call runs as — see the module's context for the order read.

        with acting_for_tool_call(billing) as identity: ...
        ->  over HTTP with "Authorization: Bearer <token of ana>": Identity(subject="ana")
        ->  over stdio, bus guarded: whoever `AccessControl` makes of no credentials
        with acting_for_tool_call(None) as identity: ...   ->  the identity already current

    1. No bus: the identity already current, unchanged.
    2. An identity FastMCP's verifier put in the access token: acted as directly.
    Final: otherwise the call's credentials — `None` over stdio — authenticated by the bus's
    `AccessControl` (`authenticated_as`), for the duration of the call.
    """
    if bus is None:
        with nullcontext(current_identity()) as unchanged:
            yield unchanged
        return
    verified, credentials = _call_state()
    if verified is not None:
        with as_identity(verified) as identity:
            yield identity
        return
    with authenticated_as(bus, credentials) as identity:
        yield identity


def token_verifier(
    access: "AccessControl[Any]",
    base_url: str | None = None,
    required_scopes: list[str] | None = None,
) -> Any:
    """A FastMCP `TokenVerifier` answering with `access`'s providers — for `FastMCP(auth=...)`,
    so FastMCP refuses a missing or failing bearer with 401 and publishes, given `base_url`, the
    resource metadata MCP clients discover the authorization server by (RFC 9728).

        FastMCP("billing", auth=token_verifier(billing_access, "https://mcp.acme.bo"))
        await verifier.verify_token("<ana's JWT>")
        ->  AccessToken(client_id="ana", subject="ana", scopes=["invoice.issue"], ...)
        await verifier.verify_token("garbage")              ->  None, FastMCP answers 401

    1. FastMCP's `AccessToken` and `TokenVerifier` imported — refused with the extra-install
       hint without `fastmcp`.
    2. A `TokenVerifier` whose `verify_token`:
       2.1 hands the token to `access` as `Authorization: Bearer` credentials, off the
           event loop;
       2.2 answers `None` for nobody authenticated, or the anonymous identity;
       2.3 otherwise answers an `AccessToken` carrying the identity under `IDENTITY_CLAIM`.
    Final: that verifier, over `base_url` and `required_scopes`.
    """
    try:
        from fastmcp.server.auth import (  # pyright: ignore[reportMissingImports]
            AccessToken,
            TokenVerifier,
        )
    except ImportError as error:
        raise ImportError(FASTMCP_MISSING) from error

    class AccessControlTokenVerifier(TokenVerifier):
        """FastMCP's bearer check, answered by the bus's `AccessControl` providers."""

        async def verify_token(self, token: str) -> Any:
            """The bearer as FastMCP's `AccessToken`, or `None` — refused with 401 — when it
            authenticates nobody.

            1. The token as `Authorization: Bearer` credentials, authenticated off the event
               loop (`access.authenticate` may block on a provider).
            2. Unauthenticated, or the anonymous identity: `None`.
            Final: an access token whose client and subject are the identity's subject, its
            scopes the permissions, and the identity itself under `IDENTITY_CLAIM` — read back
            by `_call_state`, so the call is not authenticated twice.
            """
            credentials = Credentials(
                transport="mcp", headers={"authorization": f"Bearer {token}"}
            )
            try:
                identity = await asyncio.to_thread(access.authenticate, credentials)
            except exceptions.Unauthenticated:
                return None
            if identity.is_anonymous:
                return None
            return AccessToken(
                token=token,
                client_id=identity.subject,
                scopes=sorted(identity.permissions),
                subject=identity.subject,
                claims={IDENTITY_CLAIM: identity.model_dump(mode="json")},
            )

    return AccessControlTokenVerifier(base_url=base_url, required_scopes=required_scopes)
