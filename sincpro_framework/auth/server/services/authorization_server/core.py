"""`AuthorizationServerCore`: what every capability of an OAuth authorization server stands on.

Theory — OAuth names four roles (RFC 6749 §1.1): the resource owner (the person), the client (an
app acting for them), the resource server (the API that verifies tokens — `auth.server`'s
`resource_server`) and the authorization server (this: it lets the person sign in, asks nothing
of the client's password, and issues the tokens the resource server accepts).

Design — capabilities composed, not one closed class. Each capability is a mixin over this core,
in its own module, depending on nothing but what the core holds:

    AuthorizationCodeGrant   /authorize and /token for a code       RFC 6749 §4.1, RFC 7636
    RefreshTokenGrant        /token for a refresh token             RFC 6749 §6
    TokenRevocation          /revoke                                RFC 7009
    ClientRegistration       /register                              RFC 7591
    AuthorizationServerMetadata           /.well-known/oauth-authorization-server  RFC 8414

`AuthorizationServer` composes them all; a project composes the ones it serves —
`class Server(AuthorizationCodeGrant, RefreshTokenGrant, AuthorizationServerMetadata)` — or adds one of its own
over this core. Every decision is a public method a subclass may override: `OAuthClient.redirects_to`
(how a redirect matches), `ClientRegistration.accepts_redirect` (which redirects may be registered),
`AuthorizationCodeGrant.tokens_for` (what a grant issues).

What it stands on: a `TokenIssuer` (`auth.accounts`) issues, rotates and revokes the tokens; a
`ClientRegistry` holds the clients; what waits — a pending authorization, a code — lives in the
context's `Sessions`, stored by digest, taken once, on the store every replica shares. No
transport: an HTTP route, FastMCP or a test calls these methods.
"""

import hmac
from datetime import timedelta
from typing import Any

from sincpro_framework.auth.accounts import TokenIssuer
from sincpro_framework.auth.common.domain.exceptions import OAuthError, OAuthErrorCode
from sincpro_framework.auth.common.infrastructure.digests import digest
from sincpro_framework.auth.server.adapters.authorization_server.clients import (
    KeyValueClients,
)
from sincpro_framework.auth.server.domain.authorization_server.authorization import (
    CodeChallengeMethod,
    PkcePolicy,
)
from sincpro_framework.auth.server.domain.authorization_server.clients import (
    ClientRegistry,
    GrantType,
    OAuthClient,
)
from sincpro_framework.context import Sessions, use_context


class AuthorizationServerCore:
    """An authorization server's name, its issuer of tokens, its clients and its policy."""

    def __init__(
        self,
        issuer: str,
        tokens: TokenIssuer,
        clients: ClientRegistry | None = None,
        pkce: PkcePolicy = PkcePolicy.REQUIRED,
        challenge_methods: frozenset[CodeChallengeMethod] = frozenset(
            {CodeChallengeMethod.S256}
        ),
        sign_in_within: timedelta = timedelta(minutes=10),
        code_lasts: timedelta = timedelta(minutes=1),
    ) -> None:
        """`issuer` is the URL clients know the server by (the `iss` of what it answers);
        `tokens` issues what a grant grants; `clients` the registry — on the context's store when
        not given; `pkce` which clients must send a challenge and `challenge_methods` the ones
        accepted; `sign_in_within` how long an authorization waits for the person; `code_lasts`
        how long a code may be exchanged (RFC 6749 §4.1.2: short — a minute)."""
        self.issuer = issuer
        self.tokens = tokens
        self.clients = clients if clients is not None else KeyValueClients()
        self.pkce = pkce
        self.challenge_methods = challenge_methods
        self.sign_in_within = sign_in_within
        self.code_lasts = code_lasts

    def _held(self, what: str, lasts: timedelta) -> Sessions:
        """The single-use values of `what` on the context's store, each lasting `lasts`."""
        return use_context().sessions(
            idle_for=lasts, at_most=lasts, namespace=f"oauth:{what}"
        )

    def authenticate_client(
        self, client_id: str, secret: str | None, grant: GrantType | None = None
    ) -> OAuthClient:
        """The client, once it proved who it is — RFC 6749 §2.3.

        1. Unknown, or a confidential client whose secret is wrong: `invalid_client` (401).
        2. Not registered for `grant`: `unauthorized_client`.
        Final: the client. A public client proves nothing here — its PKCE verifier does.
        """
        client = self.clients.find(client_id)
        if client is None:
            raise OAuthError(OAuthErrorCode.INVALID_CLIENT, "unknown client")
        if client.secret_digest is not None and not hmac.compare_digest(
            digest(secret or ""), client.secret_digest
        ):
            raise OAuthError(OAuthErrorCode.INVALID_CLIENT, "the client's secret is wrong")
        if grant is not None and not client.uses(grant):
            raise OAuthError(
                OAuthErrorCode.UNAUTHORIZED_CLIENT, f"the client may not use {grant.value}"
            )
        return client

    def capabilities(self) -> dict[str, Any]:
        """What this server publishes about itself — each capability composed adds its part
        (`AuthorizationServerMetadata` writes the document from it)."""
        return {"issuer": self.issuer}
