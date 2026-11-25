"""`AuthorizationServerCore`: what every capability of an OAuth authorization server stands on.

Theory — OAuth names four roles (RFC 6749 §1.1): the resource owner (the person), the client (an
app acting for them), the resource server (the API that verifies tokens — `auth.server`'s
`resource_server`) and the authorization server (this: it lets the person sign in, asks nothing
of the client's password, and issues the tokens the resource server accepts).

Design — capabilities composed, not one closed class. Each capability is a mixin over this core,
in its own module, depending on nothing but what the core holds, and saying what it adds:

    AuthorizationCodeGrant        /authorize, and /token for a code      RFC 6749 §4.1, RFC 7636
    RefreshTokenGrant             /token for a refresh token             RFC 6749 §6
    TokenRevocation               /revoke                                RFC 7009
    ClientRegistration            /register                              RFC 7591
    AuthorizationServerMetadata   /.well-known/oauth-authorization-server  RFC 8414

A capability adds its grant to `token_grants()` (the token endpoint dispatches by it), its
endpoints to `served_endpoints()` and what it publishes to `capabilities()` — so the metadata of
a server never announces an endpoint it does not serve. `AuthorizationServer` composes them all;
a project composes the ones it serves — `class Server(AuthorizationCodeGrant, RefreshTokenGrant,
AuthorizationServerMetadata)` — or adds one of its own over this core. Every decision is a public
method a subclass may override: `OAuthClient.allows_redirect_uri` (how a redirect matches),
`ClientRegistration.is_registrable_redirect_uri` (which redirects may be registered),
`AuthorizationCodeGrant.issue_tokens` (what a grant issues).

What it stands on: a `TokenIssuer` (`auth.accounts`) issues, rotates and revokes the tokens; a
`ClientRegistry` holds the clients; what waits — a pending authorization, a code — lives in the
context's `Sessions`, stored by digest, taken once, on the store every replica shares. No
transport: an HTTP route, FastMCP or a test calls these methods — `token(form, authorization)`
is the whole token endpoint.
"""

import base64
import binascii
import hmac
from collections.abc import Callable, Mapping
from datetime import timedelta
from enum import StrEnum
from typing import Any
from urllib.parse import unquote_plus

from sincpro_framework.auth.accounts import IssuedTokens, TokenIssuer
from sincpro_framework.auth.common.adapters.key_value import KeyValueStoreMixin
from sincpro_framework.auth.common.domain.exceptions import OAuthError, OAuthErrorCode
from sincpro_framework.auth.common.infrastructure.digests import digest
from sincpro_framework.auth.server.adapters.authorization_server.oauth_clients import (
    KeyValueOAuthClients,
)
from sincpro_framework.auth.server.domain.authorization_server.authorization import (
    CodeChallengeMethod,
    PkcePolicy,
)
from sincpro_framework.auth.server.domain.authorization_server.clients import (
    ClientAuthMethod,
    ClientRegistry,
    GrantType,
    OAuthClient,
)
from sincpro_framework.auth.server.domain.authorization_server.endpoints import (
    AuthorizationServerEndpoint,
)
from sincpro_framework.context import Sessions, use_context
from sincpro_framework.data_layer.store import KeyValueStore

TokenGrantHandler = Callable[[Mapping[str, str], str, str | None], IssuedTokens]
"""What the token endpoint runs for one `grant_type`: the form, the client id and its secret."""


class SingleUseValue(StrEnum):
    """What the server stores to be taken once — the namespace each lives under."""

    PENDING = "pending"
    """An authorization waiting for the person to sign in."""
    CODE = "code"
    """A code waiting to be exchanged."""


class AuthorizationServerCore(KeyValueStoreMixin):
    """An authorization server's name, its token issuer, its clients and its policy."""

    warn_when_unshared = True

    def __init__(
        self,
        issuer: str,
        token_issuer: TokenIssuer,
        client_registry: ClientRegistry | None = None,
        pkce_policy: PkcePolicy = PkcePolicy.REQUIRED,
        challenge_methods: frozenset[CodeChallengeMethod] = frozenset(
            {CodeChallengeMethod.S256}
        ),
        authorization_ttl: timedelta = timedelta(minutes=10),
        code_ttl: timedelta = timedelta(minutes=1),
        registration_token: str | None = None,
        store: KeyValueStore | None = None,
        default_auth_method: ClientAuthMethod = ClientAuthMethod.NONE,
    ) -> None:
        """`issuer` is the URL clients know the server by (the `iss` of what it answers);
        `token_issuer` issues what a grant grants; `client_registry` holds the clients — on the
        context's store when not given; `pkce_policy` says which clients must send a challenge
        and `challenge_methods` the methods accepted; `authorization_ttl` is how long an
        authorization waits for the person, and how long a code exchanged is remembered to
        catch its replay; `code_ttl` how long a code may be exchanged (RFC 6749 §4.1.2: short —
        a minute); `registration_token` the initial access token registration asks for (RFC 7591
        §3) — open registration when `None`; `store` pins the store of the pending
        authorizations, the codes and the redeemed codes instead of the running context's;
        `default_auth_method` is how a client that registers saying nothing proves itself —
        `none`, a public client, because MCP hosts register so; RFC 7591 §2 says
        `client_secret_basic`."""
        self._init_key_value_store(store, "sincpro:auth:oauth")
        self.issuer = issuer
        self.token_issuer = token_issuer
        self.client_registry = (
            client_registry if client_registry is not None else KeyValueOAuthClients()
        )
        self.pkce_policy = pkce_policy
        self.challenge_methods = challenge_methods
        self.authorization_ttl = authorization_ttl
        self.code_ttl = code_ttl
        self.registration_token = registration_token
        self.default_auth_method = default_auth_method

    def _single_use_store(self, kind: SingleUseValue) -> Sessions:
        """The values of `kind` on the context's store, each taken once and lasting its TTL."""
        ttl = self.code_ttl if kind is SingleUseValue.CODE else self.authorization_ttl
        if self._store is not None:
            return Sessions(self._store, idle_for=ttl, at_most=ttl, namespace=f"oauth:{kind}")
        return use_context().sessions(idle_for=ttl, at_most=ttl, namespace=f"oauth:{kind}")

    def authenticate_client(
        self, client_id: str, client_secret: str | None, grant: GrantType | None = None
    ) -> OAuthClient:
        """The client, once it proved who it is — RFC 6749 §2.3.

        1. Unknown, or a confidential client whose secret is wrong: `invalid_client` (401).
        2. Not registered for `grant`: `unauthorized_client`.
        Final: the client. A public client proves nothing here — its PKCE verifier does; a
        secret it sends is not looked at.
        """
        client = self.client_registry.find(client_id)
        if client is None:
            raise OAuthError(OAuthErrorCode.INVALID_CLIENT, "unknown client")
        if client.secret_digest is not None and not hmac.compare_digest(
            digest(client_secret or ""), client.secret_digest
        ):
            raise OAuthError(OAuthErrorCode.INVALID_CLIENT, "the client's secret is wrong")
        if grant is not None and not client.allows_grant(grant):
            raise OAuthError(
                OAuthErrorCode.UNAUTHORIZED_CLIENT, f"the client may not use {grant.value}"
            )
        return client

    def parse_client_credentials(
        self, parameters: Mapping[str, str], authorization: str | None = None
    ) -> tuple[str, str | None]:
        """The client id and secret a request to the token endpoint carries — the
        `Authorization: Basic` header (`client_secret_basic`, each part form-encoded, RFC 6749
        §2.3.1) or `client_id`/`client_secret` in the form (`client_secret_post`, `none`).

            parse_client_credentials({"client_id": "cl_1"})              ->  ("cl_1", None)
            parse_client_credentials({}, "Basic Y2xfMTpzM2NyM3Q=")       ->  ("cl_1", "s3cr3t")

        Both at once, a header that does not decode, or no client id: `invalid_request` /
        `invalid_client`.
        """
        if authorization is not None and authorization.lower().startswith("basic "):
            if "client_secret" in parameters:
                raise OAuthError(
                    OAuthErrorCode.INVALID_REQUEST, "the client authenticated twice"
                )
            try:
                decoded = base64.b64decode(authorization[6:].strip(), validate=True).decode()
            except (binascii.Error, UnicodeDecodeError) as malformed:
                raise OAuthError(
                    OAuthErrorCode.INVALID_CLIENT, "the Basic credentials do not decode"
                ) from malformed
            client_id, separator, secret = decoded.partition(":")
            if not separator:
                raise OAuthError(
                    OAuthErrorCode.INVALID_CLIENT, "the Basic credentials have no secret"
                )
            return unquote_plus(client_id), unquote_plus(secret)
        client_id = parameters.get("client_id", "")
        if not client_id:
            raise OAuthError(OAuthErrorCode.INVALID_CLIENT, "no client_id")
        return client_id, parameters.get("client_secret")

    def require_parameter(self, parameters: Mapping[str, str], name: str) -> str:
        """`parameters[name]` — `invalid_request` naming it when it is missing or empty."""
        value = parameters.get(name, "")
        if not value:
            raise OAuthError(OAuthErrorCode.INVALID_REQUEST, f"{name} is required")
        return value

    def token_grants(self) -> dict[GrantType, TokenGrantHandler]:
        """The grants the token endpoint answers, each with what runs it — every grant
        capability composed adds its own."""
        return {}

    def token(
        self, parameters: Mapping[str, str], authorization: str | None = None
    ) -> IssuedTokens:
        """The token endpoint — RFC 6749 §3.2: the form posted and the `Authorization` header.

            tokens = server.token(form, request.headers.get("authorization"))
            return JSONResponse(tokens.answer(), headers={"Cache-Control": "no-store"})

        A `grant_type` no capability composed serves is `unsupported_grant_type`.
        """
        handlers = self.token_grants()
        grant_type = parameters.get("grant_type", "")
        grant = next((one for one in handlers if one.value == grant_type), None)
        if grant is None:
            raise OAuthError(
                OAuthErrorCode.UNSUPPORTED_GRANT_TYPE,
                f"grant_type {grant_type!r} is not served here",
            )
        client_id, client_secret = self.parse_client_credentials(parameters, authorization)
        return handlers[grant](parameters, client_id, client_secret)

    def served_endpoints(self) -> set[AuthorizationServerEndpoint]:
        """The endpoints this server serves — the token endpoint when a grant is composed, the
        JWKS when its tokens are signed with public keys; every capability adds its own."""
        served = set()
        if self.token_grants():
            served.add(AuthorizationServerEndpoint.TOKEN)
        if self.token_issuer.keys.has_public_keys:
            served.add(AuthorizationServerEndpoint.JWKS)
        return served

    def capabilities(self) -> dict[str, Any]:
        """What this server publishes about itself — each capability composed adds its part
        (`AuthorizationServerMetadata` writes the document from it)."""
        grants = self.token_grants()
        capabilities: dict[str, Any] = {
            "issuer": self.issuer,
            "grant_types_supported": [grant.value for grant in GrantType if grant in grants],
            "token_endpoint_auth_methods_supported": [one.value for one in ClientAuthMethod],
        }
        if self.client_registry.supports_metadata_documents:
            capabilities["client_id_metadata_document_supported"] = True
        return capabilities
