"""`RefreshTokenGrant`: a client renewing its tokens with the refresh token it was given — its own
only (RFC 6749 §6); rotated, a replay revoking the session (the `TokenIssuer`'s rules)."""

from sincpro_framework.auth.accounts import IssuedTokens
from sincpro_framework.auth.common.domain.exceptions import (
    OAuthError,
    OAuthErrorCode,
    Unauthenticated,
)
from sincpro_framework.auth.server.domain.authorization_server.clients import GrantType
from sincpro_framework.auth.server.services.authorization_server.core import (
    AuthorizationServerCore,
)


class RefreshTokenGrant(AuthorizationServerCore):
    """The refresh token grant."""

    def refresh(
        self, refresh_token: str, client_id: str, client_secret: str | None = None
    ) -> IssuedTokens:
        """A new pair for `refresh_token` — `invalid_grant` when it is unknown, expired, revoked,
        replayed or another client's."""
        self.authenticate_client(client_id, client_secret, GrantType.REFRESH_TOKEN)
        try:
            return self.tokens.refresh(refresh_token, client=client_id)
        except Unauthenticated as refused:
            raise OAuthError(OAuthErrorCode.INVALID_GRANT, refused.reason) from refused
