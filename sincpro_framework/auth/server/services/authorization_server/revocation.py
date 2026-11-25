"""`TokenRevocation`: a client taking back a token it holds — its session ends, every access token
of it refused at once (RFC 7009).

Context: a client takes back its own tokens only (RFC 7009 §2.1): a token issued to another
client, like one the server never issued, is left alone and answered the same — nobody learns
which tokens exist. Served only when the `TokenIssuer` has revocations to write.
"""

from sincpro_framework.auth.server.domain.authorization_server.endpoints import (
    AuthorizationServerEndpoint,
)
from sincpro_framework.auth.server.services.authorization_server.core import (
    AuthorizationServerCore,
)


class TokenRevocation(AuthorizationServerCore):
    """Revocation of an access or a refresh token."""

    def revoke(self, token: str, client_id: str, client_secret: str | None = None) -> None:
        """The session of `token` ended, when it is this client's."""
        self.authenticate_client(client_id, client_secret)
        self.token_issuer.revoke(token, client=client_id)

    def served_endpoints(self) -> set[AuthorizationServerEndpoint]:
        served = super().served_endpoints()
        if self.token_issuer.revocations is not None:
            served.add(AuthorizationServerEndpoint.REVOCATION)
        return served

    def capabilities(self) -> dict:
        capabilities = super().capabilities()
        if self.token_issuer.revocations is not None:
            capabilities["revocation_endpoint_auth_methods_supported"] = capabilities[
                "token_endpoint_auth_methods_supported"
            ]
        return capabilities
