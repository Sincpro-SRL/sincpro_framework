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

from sincpro_framework.auth.domain.exceptions import Unauthenticated
from sincpro_framework.auth.domain.identity import Credentials, Identity
from sincpro_framework.auth.entrypoint.transports import (
    authenticated_as,
    credentials_from_asgi,
)
from sincpro_framework.auth.infrastructure.security_context import (
    as_identity,
    current_identity,
)
from sincpro_framework.entrypoints.adapters.mcp.mcp import FASTMCP_MISSING

if TYPE_CHECKING:
    from sincpro_framework.auth.entrypoint.access_control import AccessControl
    from sincpro_framework.use_bus import UseFramework

IDENTITY_CLAIM = "sincpro_identity"
"""Where a verified identity rides inside FastMCP's access token — in the process, never sent."""


def _call_state() -> tuple[Identity | None, Credentials | None]:
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
    """The identity one tool call runs as — see the module's context for the order read."""
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
    resource metadata MCP clients discover the authorization server by (RFC 9728)."""
    try:
        from fastmcp.server.auth import (  # pyright: ignore[reportMissingImports]
            AccessToken,
            TokenVerifier,
        )
    except ImportError as error:
        raise ImportError(FASTMCP_MISSING) from error

    class AccessControlTokenVerifier(TokenVerifier):
        async def verify_token(self, token: str) -> Any:
            credentials = Credentials(
                transport="mcp", headers={"authorization": f"Bearer {token}"}
            )
            try:
                identity = await asyncio.to_thread(access.authenticate, credentials)
            except Unauthenticated:
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
