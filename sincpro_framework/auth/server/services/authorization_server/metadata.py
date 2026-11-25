"""`AuthorizationServerMetadata`: what the server says about itself — RFC 8414, read by a client before it
asks for anything — built from the capabilities the server composes, so it never publishes an
endpoint it does not serve."""

from typing import Any

from sincpro_framework.auth.server.domain.authorization_server.clients import GrantType
from sincpro_framework.auth.server.services.authorization_server.authorization_code import (
    AuthorizationCodeGrant,
)
from sincpro_framework.auth.server.services.authorization_server.core import (
    AuthorizationServerCore,
)
from sincpro_framework.auth.server.services.authorization_server.refresh import (
    RefreshTokenGrant,
)
from sincpro_framework.auth.server.services.authorization_server.registration import (
    ClientRegistration,
)
from sincpro_framework.auth.server.services.authorization_server.revocation import (
    TokenRevocation,
)


class AuthorizationServerMetadata(AuthorizationServerCore):
    """The authorization server metadata document."""

    def metadata(self, endpoints: dict[str, str]) -> dict[str, Any]:
        """The document, with the URLs the transport serves each endpoint at — `authorization`,
        `token`, `registration`, `revocation`, `jwks` — only those of a capability composed.

            server.metadata({"authorization": f"{base}/authorize", "token": f"{base}/token"})
        """
        grants = [
            grant.value
            for grant, capability in (
                (GrantType.AUTHORIZATION_CODE, AuthorizationCodeGrant),
                (GrantType.REFRESH_TOKEN, RefreshTokenGrant),
            )
            if isinstance(self, capability)
        ]
        served = {
            "authorization": isinstance(self, AuthorizationCodeGrant),
            "token": bool(grants),
            "registration": isinstance(self, ClientRegistration),
            "revocation": isinstance(self, TokenRevocation),
            "jwks": True,
        }
        document = {
            **self.capabilities(),
            "grant_types_supported": grants,
            "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
        }
        for name, url in endpoints.items():
            if served.get(name):
                document[f"{name}_endpoint" if name != "jwks" else "jwks_uri"] = url
        return document
