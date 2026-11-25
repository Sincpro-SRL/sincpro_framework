"""`AuthorizationCodeGrant`: the authorization code grant — RFC 6749 §4.1 with PKCE (RFC 7636)
as the server's policy asks — from `/authorize` to the tokens.

    pending = server.authorize(request)               the login page shows pending.client_name
    redirect = server.complete(pending.id, identity)  the browser goes there: ?code=…&state=…&iss=…
    redirect = server.deny(pending.id)                ?error=access_denied&state=…&iss=…
    tokens = server.exchange_code(code, verifier, client_id, redirect_uri)

Context: the framework never shows a login page — the project's does, and says who signed in by
calling `complete`. A request whose client is unknown or whose redirect is not one it registered
is refused without redirecting: a redirect nobody verified would make the server an open
redirector (RFC 6749 §4.1.2.1). Past that, a refusal carries `redirect_uri`, the client's
redirect with the error, where the transport sends the browser. A code works once, for its
client, its redirect and its verifier; a code exchanged and presented again is a code stolen,
and the session it opened is revoked (RFC 6749 §4.1.2).

Consent: a client registered by anyone (open registration) may send a person here. When the
project's login completes at once for someone already signed in, show which client asks
(`PendingAuthorization.client_name`) and let the person accept or `deny` — otherwise a
registered impostor receives a code for a person who never saw it.

Extend: override `issue_tokens` to issue differently — another issuer per resource, claims
stored server-side —, `OAuthClient.allows_redirect_uri` to match redirects another way.
"""

import json
from collections.abc import Mapping
from datetime import timedelta
from urllib.parse import urlsplit

from sincpro_framework.auth.accounts import IssuedTokens
from sincpro_framework.auth.common.domain.exceptions import OAuthError, OAuthErrorCode
from sincpro_framework.auth.common.domain.identity import Assurance, Identity
from sincpro_framework.auth.common.infrastructure.digests import digest
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
from sincpro_framework.auth.server.domain.authorization_server.endpoints import (
    AuthorizationServerEndpoint,
)
from sincpro_framework.auth.server.services.authorization_server.core import (
    AuthorizationServerCore,
    SingleUseValue,
    TokenGrantHandler,
)


class AuthorizationCodeGrant(AuthorizationServerCore):
    """The authorization code grant."""

    def _validate_request(self, request: AuthorizationRequest, client: OAuthClient) -> None:
        """What a request must meet once its client and redirect are known — `OAuthError` else.

        1. `response_type=code`.
        2. A challenge when the `PkcePolicy` asks this client for one, by a method accepted.
        3. The grant and every scope, ones the client may use.
        4. A `resource`, when given, an absolute URI with no fragment (RFC 8707 §2).
        """
        if request.response_type != "code":
            raise OAuthError(
                OAuthErrorCode.UNSUPPORTED_RESPONSE_TYPE,
                f"response_type {request.response_type!r} is not served; use code",
            )
        pkce_required = self.pkce_policy is PkcePolicy.REQUIRED or (
            self.pkce_policy is PkcePolicy.PUBLIC_CLIENTS and client.is_public
        )
        if pkce_required and not request.code_challenge:
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
        if not client.allows_grant(GrantType.AUTHORIZATION_CODE):
            raise OAuthError(
                OAuthErrorCode.UNAUTHORIZED_CLIENT,
                "the client may not use authorization_code",
            )
        if not client.allows_scopes(request.scopes):
            raise OAuthError(
                OAuthErrorCode.INVALID_SCOPE, "a scope the client may not ask for"
            )
        if request.resource is not None:
            parts = urlsplit(request.resource)
            if not parts.scheme or not parts.netloc or "#" in request.resource:
                raise OAuthError(
                    OAuthErrorCode.INVALID_TARGET,
                    "resource is an absolute URI with no fragment",
                )

    def authorize(self, request: AuthorizationRequest) -> PendingAuthorization:
        """A pending authorization for `request`, for the person to sign in to.

        1. The client known and the redirect one it registered — else refused, never redirected.
        2. What the server's policy and the client's registration ask (`_validate_request`) —
           refused with `redirect_uri` set: the client hears the error at its redirect.
        Final: the authorization, waiting `authorization_ttl` under a new id.
        """
        client = self.client_registry.find(request.client_id)
        if client is None:
            raise OAuthError(OAuthErrorCode.INVALID_CLIENT, "unknown client")
        if not client.allows_redirect_uri(request.redirect_uri):
            raise OAuthError(
                OAuthErrorCode.INVALID_REQUEST,
                "redirect_uri is not one the client registered",
            )
        try:
            self._validate_request(request, client)
        except OAuthError as refused:
            refused.redirect_uri = request.build_redirect_uri(
                {
                    "error": refused.error.value,
                    "error_description": refused.description,
                    "state": request.state,
                    "iss": self.issuer,
                }
            )
            raise
        pending = self._single_use_store(SingleUseValue.PENDING).open(
            {"request": request.model_dump(mode="json"), "client_name": client.name}
        )
        return PendingAuthorization(id=pending.id, request=request, client_name=client.name)

    def find_pending(self, pending_id: str) -> PendingAuthorization | None:
        """The authorization waiting under `pending_id` — `None` once completed, denied or
        expired. What a login page reads to say who is asking."""
        pending = self._single_use_store(SingleUseValue.PENDING).get(pending_id)
        if pending is None:
            return None
        return PendingAuthorization(
            id=pending_id,
            request=AuthorizationRequest.model_validate(pending.values["request"]),
            client_name=pending.values.get("client_name", ""),
        )

    def _pop_pending(self, pending_id: str) -> AuthorizationRequest:
        """The request waiting under `pending_id`, removed — once, whoever asks."""
        pending = self._single_use_store(SingleUseValue.PENDING).take(pending_id)
        if pending is None:
            raise OAuthError(
                OAuthErrorCode.INVALID_REQUEST,
                "the authorization expired or was already used",
            )
        return AuthorizationRequest.model_validate(pending.values["request"])

    def complete(
        self, pending_id: str, identity: Identity, assurance: Assurance | None = None
    ) -> str:
        """The person signed in as `identity` — how strongly, `assurance`: a code for the client,
        and the URL the browser goes to with it (`?code=…&state=…&iss=…`, RFC 9207)."""
        request = self._pop_pending(pending_id)
        authorization_code = AuthorizationCode(
            request=request, identity=identity, assurance=assurance or identity.assurance
        )
        code = (
            self._single_use_store(SingleUseValue.CODE)
            .open(authorization_code.model_dump(mode="json"))
            .id
        )
        return request.build_redirect_uri(
            {"code": code, "state": request.state, "iss": self.issuer}
        )

    def deny(self, pending_id: str) -> str:
        """The person refused: the URL the browser goes to with `error=access_denied`."""
        request = self._pop_pending(pending_id)
        return request.build_redirect_uri(
            {
                "error": OAuthErrorCode.ACCESS_DENIED.value,
                "state": request.state,
                "iss": self.issuer,
            }
        )

    def issue_tokens(
        self, authorization_code: AuthorizationCode, client: OAuthClient
    ) -> IssuedTokens:
        """What a code grants: a new session, for whoever signed in, to this client, with the
        scopes asked, for the resource asked (`aud`, RFC 8707). Override to issue differently.
        """
        return self.token_issuer.issue(
            authorization_code.identity,
            scopes=authorization_code.request.scopes,
            assurance=authorization_code.assurance,
            client=client.client_id,
            audience=authorization_code.request.resource,
        )

    def _revoke_replayed_code(self, code: str) -> None:
        """A code presented after it was exchanged: the session it opened is revoked — its
        tokens were issued to whoever exchanged it first, maybe a thief (RFC 6749 §4.1.2)."""
        redeemed_key = f"{self.prefix}:redeemed:{digest(code)}"
        redeemed = self.store().get(redeemed_key)
        if redeemed is None or self.token_issuer.revocations is None:
            return
        self.store().delete(redeemed_key)
        opened = json.loads(redeemed)
        self.token_issuer.revoke_session(opened["session"], opened["subject"])

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
        2. The code taken — once, whatever follows: unknown, used or expired is `invalid_grant`;
           one already exchanged revokes the session it opened.
        3. Issued for this client and this redirect, and the verifier proves its challenge —
           `invalid_grant` otherwise.
        Final: `issue_tokens` for the code, its exchange remembered `authorization_ttl` to catch
        a replay.
        """
        client = self.authenticate_client(
            client_id, client_secret, GrantType.AUTHORIZATION_CODE
        )
        taken = self._single_use_store(SingleUseValue.CODE).take(code)
        if taken is None:
            self._revoke_replayed_code(code)
            raise OAuthError(
                OAuthErrorCode.INVALID_GRANT, "the code is unknown, used or expired"
            )
        authorization_code = AuthorizationCode.model_validate(taken.values)
        if (
            authorization_code.request.client_id != client_id
            or authorization_code.request.redirect_uri != redirect_uri
        ):
            raise OAuthError(
                OAuthErrorCode.INVALID_GRANT, "the code was issued for another request"
            )
        if not authorization_code.verify_pkce(code_verifier):
            raise OAuthError(
                OAuthErrorCode.INVALID_GRANT, "the code_verifier does not prove the code"
            )
        tokens = self.issue_tokens(authorization_code, client)
        self.store().set(
            f"{self.prefix}:redeemed:{digest(code)}",
            json.dumps(
                {"session": tokens.session, "subject": authorization_code.identity.subject}
            ).encode(),
            max(self.authorization_ttl, timedelta(seconds=1)),
        )
        return tokens

    def _exchange_code_form(
        self, parameters: Mapping[str, str], client_id: str, client_secret: str | None
    ) -> IssuedTokens:
        """`exchange_code` from the token endpoint's form."""
        return self.exchange_code(
            self.require_parameter(parameters, "code"),
            parameters.get("code_verifier"),
            client_id,
            self.require_parameter(parameters, "redirect_uri"),
            client_secret,
        )

    def token_grants(self) -> dict[GrantType, TokenGrantHandler]:
        return {
            **super().token_grants(),
            GrantType.AUTHORIZATION_CODE: self._exchange_code_form,
        }

    def served_endpoints(self) -> set[AuthorizationServerEndpoint]:
        return super().served_endpoints() | {AuthorizationServerEndpoint.AUTHORIZATION}

    def capabilities(self) -> dict:
        return {
            **super().capabilities(),
            "response_types_supported": ["code"],
            "code_challenge_methods_supported": sorted(
                one.value for one in self.challenge_methods
            ),
        }
