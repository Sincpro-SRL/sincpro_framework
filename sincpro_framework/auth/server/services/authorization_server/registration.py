"""`ClientRegistration`: a client registering itself — RFC 7591 dynamic registration, what an MCP
host does the first time it connects.

    registered = server.register(["https://claude.ai/api/mcp/auth_callback"], name="Claude")
    registered.client.client_id     ->  "cl_9f…"
    registered.secret               ->  None: a public client, PKCE alone

Context: a redirect is `https`, a loopback `http` (a desktop app, RFC 8252), or a private-use
scheme with a dot (`com.example.app:/callback`); never a fragment. A confidential client's secret
is shown once, here, and kept by its digest.
"""

import ipaddress
from collections.abc import Iterable
from urllib.parse import urlsplit

from pydantic import ConfigDict

from sincpro_framework.auth.common.domain.exceptions import OAuthError, OAuthErrorCode
from sincpro_framework.auth.common.infrastructure.digests import digest, random_token
from sincpro_framework.auth.server.domain.authorization_server.clients import (
    GrantType,
    OAuthClient,
)
from sincpro_framework.auth.server.services.authorization_server.core import (
    AuthorizationServerCore,
)
from sincpro_framework.sincpro_abstractions import DataTransferObject


class RegisteredClient(DataTransferObject):
    """A client just registered, and its secret — shown once, `None` for a public client."""

    model_config = ConfigDict(frozen=True)

    client: OAuthClient
    secret: str | None = None


class ClientRegistration(AuthorizationServerCore):
    """Dynamic client registration."""

    def accepts_redirect(self, uri: str) -> bool:
        """Whether `uri` may be registered as a redirect — https; http to a loopback address (a
        desktop app, RFC 8252 §7.3); a private-use scheme with a dot (a mobile app, RFC 8252
        §7.1); never a fragment (RFC 6749 §3.1.2). Override to accept others."""
        parts = urlsplit(uri)
        if parts.fragment or not parts.scheme:
            return False
        if parts.scheme == "https":
            return bool(parts.netloc)
        if parts.scheme == "http":
            host = parts.hostname or ""
            if host == "localhost":
                return True
            try:
                return ipaddress.ip_address(host).is_loopback
            except ValueError:
                return False
        return "." in parts.scheme

    def register(
        self,
        redirect_uris: Iterable[str],
        name: str = "",
        grant_types: Iterable[GrantType] = (
            GrantType.AUTHORIZATION_CODE,
            GrantType.REFRESH_TOKEN,
        ),
        scopes: Iterable[str] = (),
        confidential: bool = False,
    ) -> RegisteredClient:
        """A new client — `invalid_redirect_uri` for none or one that may not be a redirect."""
        uris = tuple(redirect_uris)
        if not uris or not all(self.accepts_redirect(one) for one in uris):
            raise OAuthError(
                OAuthErrorCode.INVALID_REDIRECT_URI,
                "redirect_uris are https, loopback http or a private-use scheme, with no fragment",
            )
        secret = random_token("cs_") if confidential else None
        client = OAuthClient(
            client_id=random_token("cl_", 16),
            redirect_uris=uris,
            name=name,
            grant_types=frozenset(grant_types),
            scopes=frozenset(scopes),
            secret_digest=digest(secret) if secret else None,
        )
        self.clients.save(client)
        return RegisteredClient(client=client, secret=secret)
