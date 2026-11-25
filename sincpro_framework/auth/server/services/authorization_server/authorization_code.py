"""`AuthorizationCodeGrant`: the authorization code grant — RFC 6749 §4.1 with PKCE (RFC 7636)
as the server's policy asks — from `/authorize` to the tokens.

    pending = server.authorize(request)               the login page shows pending.client_name
    redirect = server.complete(pending.id, identity)  the browser goes there: ?code=…&state=…&iss=…
    redirect = server.deny(pending.id)                ?error=access_denied&state=…&iss=…
    tokens = server.exchange_code(code, verifier, client_id, redirect_uri)

Context: the framework never shows a login page — the project's does, and says who signed in by
calling `complete`. A request whose client is unknown or whose redirect is not one it registered
is refused without redirecting: a redirect nobody verified would make the server an open
redirector (RFC 6749 §4.1.2.1). A code works once, for its client, its redirect and its verifier;
a second use finds nothing.

Extend: override `tokens_for` to issue differently — another issuer per resource, claims kept
server-side —, `OAuthClient.redirects_to` to match redirects another way.
"""

from sincpro_framework.auth.accounts import IssuedTokens
from sincpro_framework.auth.common.domain.exceptions import OAuthError, OAuthErrorCode
from sincpro_framework.auth.common.domain.identity import Assurance, Identity
from sincpro_framework.auth.server.domain.authorization_server.authorization import (
    AuthorizationCode,
    AuthorizationRequest,
    PendingAuthorization,
    PkcePolicy,
)
from sincpro_framework.auth.server.domain.authorization_server.clients import (
    GrantType,
    OAuthClient,
)
from sincpro_framework.auth.server.services.authorization_server.core import (
    AuthorizationServerCore,
)


class AuthorizationCodeGrant(AuthorizationServerCore):
    """The authorization code grant."""

    def _checked(self, request: AuthorizationRequest, client: OAuthClient) -> None:
        """What a request must meet once its client and redirect are known — `OAuthError` else.

        1. A challenge when the `PkcePolicy` asks this client for one, by a method accepted.
        2. The grant and every scope, ones the client may use.
        """
        asked = self.pkce is PkcePolicy.REQUIRED or (
            self.pkce is PkcePolicy.PUBLIC_CLIENTS and client.is_public
        )
        if asked and not request.code_challenge:
            raise OAuthError(
                OAuthErrorCode.INVALID_REQUEST, "a PKCE code_challenge is required"
            )
        if (
            request.code_challenge
            and request.code_challenge_method not in self.challenge_methods
        ):
            raise OAuthError(
                OAuthErrorCode.INVALID_REQUEST,
                f"code_challenge_method {request.code_challenge_method.value} is not accepted",
            )
        if not client.uses(GrantType.AUTHORIZATION_CODE):
            raise OAuthError(
                OAuthErrorCode.UNAUTHORIZED_CLIENT,
                "the client may not use authorization_code",
            )
        if not client.may_ask_for(request.scopes):
            raise OAuthError(
                OAuthErrorCode.INVALID_SCOPE, "a scope the client may not ask for"
            )

    def authorize(self, request: AuthorizationRequest) -> PendingAuthorization:
        """A pending authorization for `request`, for the person to sign in to.

        1. The client known and the redirect one it registered — else refused, never redirected.
        2. What the server's policy and the client's registration ask (`_checked`).
        Final: the authorization, waiting `sign_in_within` under a new id.
        """
        client = self.clients.find(request.client_id)
        if client is None:
            raise OAuthError(OAuthErrorCode.INVALID_CLIENT, "unknown client")
        if not client.redirects_to(request.redirect_uri):
            raise OAuthError(
                OAuthErrorCode.INVALID_REQUEST,
                "redirect_uri is not one the client registered",
            )
        self._checked(request, client)
        held = self._held("pending", self.sign_in_within).open(
            {"request": request.model_dump(mode="json"), "client_name": client.name}
        )
        return PendingAuthorization(id=held.id, request=request, client_name=client.name)

    def pending(self, pending_id: str) -> PendingAuthorization | None:
        """The authorization waiting under `pending_id` — `None` once completed, denied or
        expired. What a login page reads to say who is asking."""
        held = self._held("pending", self.sign_in_within).get(pending_id)
        if held is None:
            return None
        return PendingAuthorization(
            id=pending_id,
            request=AuthorizationRequest.model_validate(held.values["request"]),
            client_name=held.values.get("client_name", ""),
        )

    def _taken(self, pending_id: str) -> AuthorizationRequest:
        """The request waiting under `pending_id`, taken — once, whoever asks."""
        held = self._held("pending", self.sign_in_within).take(pending_id)
        if held is None:
            raise OAuthError(
                OAuthErrorCode.INVALID_REQUEST,
                "the authorization expired or was already used",
            )
        return AuthorizationRequest.model_validate(held.values["request"])

    def complete(
        self, pending_id: str, identity: Identity, assurance: Assurance | None = None
    ) -> str:
        """The person signed in as `identity` — how strongly, `assurance`: a code for the client,
        and the URL the browser goes to with it (`?code=…&state=…&iss=…`, RFC 9207)."""
        request = self._taken(pending_id)
        granted = AuthorizationCode(
            request=request, identity=identity, assurance=assurance or identity.assurance
        )
        code = self._held("code", self.code_lasts).open(granted.model_dump(mode="json")).id
        return request.redirect_with(
            {"code": code, "state": request.state, "iss": self.issuer}
        )

    def deny(self, pending_id: str) -> str:
        """The person refused: the URL the browser goes to with `error=access_denied`."""
        request = self._taken(pending_id)
        return request.redirect_with(
            {
                "error": OAuthErrorCode.ACCESS_DENIED.value,
                "state": request.state,
                "iss": self.issuer,
            }
        )

    def tokens_for(self, granted: AuthorizationCode, client: OAuthClient) -> IssuedTokens:
        """What a code grants: a new session, for whoever signed in, to this client, with the
        scopes asked. Override to issue differently."""
        return self.tokens.issue(
            granted.identity,
            scopes=granted.request.scopes,
            assurance=granted.assurance,
            client=client.client_id,
        )

    def exchange_code(
        self,
        code: str,
        code_verifier: str | None,
        client_id: str,
        redirect_uri: str,
        client_secret: str | None = None,
    ) -> IssuedTokens:
        """The tokens a code stands for — RFC 6749 §4.1.3.

        1. The client proves itself and may use the grant (`authenticate_client`).
        2. The code taken — once, whatever follows: unknown, used or expired is `invalid_grant`.
        3. Issued for this client and this redirect, and the verifier proves its challenge —
           `invalid_grant` otherwise.
        Final: `tokens_for` the code.
        """
        client = self.authenticate_client(
            client_id, client_secret, GrantType.AUTHORIZATION_CODE
        )
        held = self._held("code", self.code_lasts).take(code)
        if held is None:
            raise OAuthError(
                OAuthErrorCode.INVALID_GRANT, "the code is unknown, used or expired"
            )
        granted = AuthorizationCode.model_validate(held.values)
        if (
            granted.request.client_id != client_id
            or granted.request.redirect_uri != redirect_uri
        ):
            raise OAuthError(
                OAuthErrorCode.INVALID_GRANT, "the code was issued for another request"
            )
        if not granted.verifies(code_verifier):
            raise OAuthError(
                OAuthErrorCode.INVALID_GRANT, "the code_verifier does not prove the code"
            )
        return self.tokens_for(granted, client)

    def capabilities(self) -> dict:
        return {
            **super().capabilities(),
            "response_types_supported": ["code"],
            "code_challenge_methods_supported": sorted(
                one.value for one in self.challenge_methods
            ),
        }
