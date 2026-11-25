"""`TokenRevocation`: a client taking back a token it holds — its session ends, every access token
of it refused at once (RFC 7009)."""

from sincpro_framework.auth.server.services.authorization_server.core import (
    AuthorizationServerCore,
)


class TokenRevocation(AuthorizationServerCore):
    """Revocation of an access or a refresh token."""

    def revoke(self, token: str, client_id: str, client_secret: str | None = None) -> None:
        """The session of `token` ended. A token the server did not issue is left alone and the
        answer is the same: nobody learns which tokens exist."""
        self.authenticate_client(client_id, client_secret)
        self.tokens.revoke(token)

    def capabilities(self) -> dict:
        return {
            **super().capabilities(),
            "revocation_endpoint_auth_methods_supported": ["none", "client_secret_post"],
        }
