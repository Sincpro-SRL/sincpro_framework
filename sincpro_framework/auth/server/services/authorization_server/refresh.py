"""`RefreshTokenGrant`: a client renewing its tokens with the refresh token it was given — its own
only (RFC 6749 §6); rotated, a replay revoking the session (the `TokenIssuer`'s rules).

Context: served only when the `TokenIssuer` stores refresh tokens — a server over an issuer
without them neither answers `grant_type=refresh_token` nor publishes it.
"""

from collections.abc import Mapping

from sincpro_framework.auth.accounts import IssuedTokens
from sincpro_framework.auth.common.domain.exceptions import (
    OAuthError,
    OAuthErrorCode,
    Unauthenticated,
)
from sincpro_framework.auth.server.domain.authorization_server.clients import GrantType
from sincpro_framework.auth.server.services.authorization_server.core import (
    AuthorizationServerCore,
    TokenGrantHandler,
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
            return self.token_issuer.refresh(refresh_token, client=client_id)
        except Unauthenticated as refused:
            raise OAuthError(OAuthErrorCode.INVALID_GRANT, refused.reason) from refused

    def _refresh_form(
        self, parameters: Mapping[str, str], client_id: str, client_secret: str | None
    ) -> IssuedTokens:
        """`refresh` from the token endpoint's form."""
        return self.refresh(
            self.require_parameter(parameters, "refresh_token"), client_id, client_secret
        )

    def token_grants(self) -> dict[GrantType, TokenGrantHandler]:
        grants = super().token_grants()
        if self.token_issuer.refresh_tokens is not None:
            grants[GrantType.REFRESH_TOKEN] = self._refresh_form
        return grants
