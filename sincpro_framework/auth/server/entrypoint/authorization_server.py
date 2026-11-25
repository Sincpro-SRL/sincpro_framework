"""`AuthorizationServer`: an OAuth 2.1 authorization server — the framework's composition of every
capability, over the service's own `TokenIssuer`.

    server = AuthorizationServer("https://auth.colegio.bo", token_issuer=issuer)

    /register   registered = server.register_from_metadata(body, initial_access_token)
    /authorize  pending = server.authorize(AuthorizationRequest(client_id=…, redirect_uri=…,
                                                                code_challenge=…, state=…))
                redirect = server.complete(pending.id, Identity.user("user:ana"))  the login said who
    /token      tokens = server.token(form, headers.get("authorization"))   code or refresh
    /revoke     server.revoke(token, client_id, client_secret)
    /.well-known/oauth-authorization-server
                server.metadata({"authorization": …, "token": …, "registration": …, …})

What it complies with: RFC 6749 (§4.1 the code grant, §6 refresh, §2.3.1 client_secret_basic,
§5.2 errors, §4.1.2.1 errors at the redirect), RFC 7636 PKCE — required of every client by
default, as OAuth 2.1 —, RFC 7591 dynamic registration, RFC 7009 revocation of a client's own
tokens, RFC 8414 metadata, RFC 9207 `iss` in the answer, RFC 8707 `resource` as the token's
audience, RFC 8252 loopback redirects on any port. The tokens are the `TokenIssuer`'s: short
access tokens other services verify with its JWKS, refresh tokens rotated with replay detection
and bound to their client; a code replayed revokes the session it opened.

What it leaves to the project: the login and consent page (`complete` / `deny` are what it
calls), where the clients live (`ClientRegistry`), and the transport — HTTP routes, FastMCP or a
test call these methods; `OAuthError.answer()` is the error body every transport sends, and its
`redirect_uri`, when set, is where the browser goes instead.

Compose your own: a server is the capabilities it inherits, each a mixin over
`AuthorizationServerCore` —

    class CodeOnly(AuthorizationCodeGrant, RefreshTokenGrant, AuthorizationServerMetadata):
        '''No dynamic registration, no revocation endpoint: metadata publishes neither.'''

— and policy is a constructor argument, never a fork: `pkce_policy=PkcePolicy.PUBLIC_CLIENTS` for
confidential clients that predate PKCE, `challenge_methods={S256, PLAIN}` for a client that cannot
hash, `registration_token=` to close registration. Every decision is a public method to override:
`issue_tokens`, `is_registrable_redirect_uri`, `authenticate_client`,
`OAuthClient.allows_redirect_uri`.
"""

from sincpro_framework.auth.server.services.authorization_server.authorization_code import (
    AuthorizationCodeGrant,
)
from sincpro_framework.auth.server.services.authorization_server.metadata import (
    AuthorizationServerMetadata,
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


class AuthorizationServer(
    AuthorizationCodeGrant,
    RefreshTokenGrant,
    TokenRevocation,
    ClientRegistration,
    AuthorizationServerMetadata,
):
    """Every capability of an OAuth authorization server — compose fewer for a server that serves
    fewer."""
