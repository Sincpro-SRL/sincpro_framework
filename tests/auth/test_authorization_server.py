"""An OAuth authorization server over the service's own token issuer: the code grant with PKCE as
its policy asks, a code used once, refresh bound to its client, revocation, dynamic registration
of safe redirects only, and metadata that publishes only the capabilities a server composes.
"""

import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import pytest

from sincpro_framework.auth import (
    Credentials,
    Identity,
    InMemoryRevocations,
    OAuthError,
    OAuthErrorCode,
    Unauthenticated,
)
from sincpro_framework.auth.accounts import InMemoryRefreshTokens, SigningKeys, TokenIssuer
from sincpro_framework.auth.server import (
    AuthorizationCodeGrant,
    AuthorizationRequest,
    AuthorizationServer,
    AuthorizationServerMetadata,
    CodeChallengeMethod,
    InMemoryClients,
    JwtProvider,
    PkcePolicy,
    RefreshTokenGrant,
)

ISSUER = "https://auth.colegio.bo"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"
VERIFIER = "a-verifier-of-forty-three-characters-or-more-0123"
ANA = Identity.user("user:ana", tenant="cauor")


def challenge_of(verifier: str) -> str:
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )


def server(**options) -> AuthorizationServer:
    tokens = TokenIssuer(
        ISSUER,
        audience="colegio",
        keys=SigningKeys.hmac({"2026-11": "a token secret of thirty-two bytes!"}),
        refresh_tokens=InMemoryRefreshTokens(),
        revocations=InMemoryRevocations(),
    )
    return AuthorizationServer(ISSUER, tokens=tokens, clients=InMemoryClients(), **options)


def asked(client_id: str, **fields) -> AuthorizationRequest:
    defaults = {
        "redirect_uri": CALLBACK,
        "code_challenge": challenge_of(VERIFIER),
        "state": "xyz",
    }
    return AuthorizationRequest(client_id=client_id, **{**defaults, **fields})


def code_from(redirect: str) -> str:
    return parse_qs(urlsplit(redirect).query)["code"][0]


def signed_in(oauth: AuthorizationServer, client_id: str, **fields) -> str:
    """The code the browser brings back once Ana signed in."""
    pending = oauth.authorize(asked(client_id, **fields))
    return code_from(oauth.complete(pending.id, ANA))


def refused(error: OAuthErrorCode) -> pytest.RaisesExc:
    return pytest.raises(OAuthError, match=f"^{error.value}")


def test_a_client_registers_a_person_signs_in_and_the_code_becomes_tokens_the_api_accepts():
    oauth = server()
    client = oauth.register([CALLBACK], name="Claude").client
    pending = oauth.authorize(asked(client.client_id, scopes=frozenset({"dues"})))
    shown = oauth.pending(pending.id)
    assert shown is not None and shown.client_name == "Claude"  # what the login page shows

    redirect = oauth.complete(pending.id, ANA)

    answer = parse_qs(urlsplit(redirect).query)
    assert (
        redirect.startswith(CALLBACK)
        and answer["state"] == ["xyz"]
        and answer["iss"] == [ISSUER]
    )
    tokens = oauth.exchange_code(answer["code"][0], VERIFIER, client.client_id, CALLBACK)
    bearer = {"authorization": f"Bearer {tokens.access_token.get_secret_value()}"}
    identity = JwtProvider.for_token_issuer(oauth.tokens).authenticate(
        Credentials(transport="http", headers=bearer)
    )
    assert identity is not None and (identity.subject, identity.client) == (
        "user:ana",
        client.client_id,
    )
    assert tokens.scope == {"dues"}


def test_a_code_works_once_and_only_with_its_verifier_client_and_redirect():
    oauth = server()
    client = oauth.register([CALLBACK, "https://claude.ai/other"]).client
    other = oauth.register([CALLBACK]).client

    for wrong in (
        lambda code: oauth.exchange_code(
            code, "another-verifier-entirely-0000000000000000", client.client_id, CALLBACK
        ),
        lambda code: oauth.exchange_code(
            code, VERIFIER, client.client_id, "https://claude.ai/other"
        ),
        lambda code: oauth.exchange_code(code, VERIFIER, other.client_id, CALLBACK),
    ):
        with refused(OAuthErrorCode.INVALID_GRANT):
            wrong(signed_in(oauth, client.client_id))
    code = signed_in(oauth, client.client_id)
    assert oauth.exchange_code(code, VERIFIER, client.client_id, CALLBACK)
    with refused(OAuthErrorCode.INVALID_GRANT):
        oauth.exchange_code(code, VERIFIER, client.client_id, CALLBACK)


def test_an_unknown_client_or_an_unregistered_redirect_is_refused_never_redirected():
    oauth = server()
    client = oauth.register([CALLBACK]).client
    with refused(OAuthErrorCode.INVALID_CLIENT):
        oauth.authorize(asked("cl_nobody"))
    with refused(OAuthErrorCode.INVALID_REQUEST):
        oauth.authorize(
            asked(client.client_id).model_copy(update={"redirect_uri": CALLBACK + "/"})
        )


def test_pkce_is_required_of_every_client_by_default_and_s256_is_the_method():
    oauth = server()
    client = oauth.register([CALLBACK], confidential=True).client
    with refused(OAuthErrorCode.INVALID_REQUEST):
        oauth.authorize(asked(client.client_id, code_challenge=""))
    with refused(OAuthErrorCode.INVALID_REQUEST):
        oauth.authorize(
            asked(client.client_id, code_challenge_method=CodeChallengeMethod.PLAIN)
        )


def test_a_server_may_ask_pkce_of_public_clients_only_or_accept_plain():
    oauth = server(
        pkce=PkcePolicy.PUBLIC_CLIENTS,
        challenge_methods=frozenset({CodeChallengeMethod.S256, CodeChallengeMethod.PLAIN}),
    )
    confidential = oauth.register([CALLBACK], confidential=True)
    public = oauth.register([CALLBACK]).client

    code = signed_in(oauth, confidential.client.client_id, code_challenge="")
    assert oauth.exchange_code(
        code, None, confidential.client.client_id, CALLBACK, confidential.secret
    )
    with refused(OAuthErrorCode.INVALID_REQUEST):
        oauth.authorize(asked(public.client_id, code_challenge=""))
    code = signed_in(
        oauth,
        public.client_id,
        code_challenge=VERIFIER,
        code_challenge_method=CodeChallengeMethod.PLAIN,
    )
    assert oauth.exchange_code(code, VERIFIER, public.client_id, CALLBACK)


def test_with_pkce_optional_a_request_without_a_challenge_is_answered():
    oauth = server(pkce=PkcePolicy.OPTIONAL)
    client = oauth.register([CALLBACK]).client
    code = signed_in(oauth, client.client_id, code_challenge="")
    assert oauth.exchange_code(code, None, client.client_id, CALLBACK)


def test_a_person_who_refuses_sends_the_client_access_denied_and_the_authorization_is_gone():
    oauth = server()
    client = oauth.register([CALLBACK]).client
    pending = oauth.authorize(asked(client.client_id))

    answer = parse_qs(urlsplit(oauth.deny(pending.id)).query)

    assert answer["error"] == ["access_denied"] and answer["state"] == ["xyz"]
    assert oauth.pending(pending.id) is None
    with refused(OAuthErrorCode.INVALID_REQUEST):
        oauth.complete(pending.id, ANA)


def test_a_scope_the_client_may_not_ask_for_is_refused():
    oauth = server()
    client = oauth.register([CALLBACK], scopes={"dues"}).client
    with refused(OAuthErrorCode.INVALID_SCOPE):
        oauth.authorize(asked(client.client_id, scopes=frozenset({"admin"})))


def test_a_refresh_token_renews_for_its_own_client_only():
    oauth = server()
    client = oauth.register([CALLBACK]).client
    other = oauth.register([CALLBACK]).client
    tokens = oauth.exchange_code(
        signed_in(oauth, client.client_id), VERIFIER, client.client_id, CALLBACK
    )
    assert tokens.refresh_token is not None
    refresh = tokens.refresh_token.get_secret_value()

    with refused(OAuthErrorCode.INVALID_GRANT):
        oauth.refresh(refresh, other.client_id)
    assert oauth.refresh(refresh, client.client_id).session == tokens.session  # left unspent


def test_a_revoked_token_ends_its_session_at_once():
    oauth = server()
    client = oauth.register([CALLBACK]).client
    tokens = oauth.exchange_code(
        signed_in(oauth, client.client_id), VERIFIER, client.client_id, CALLBACK
    )

    assert tokens.refresh_token is not None
    oauth.revoke(tokens.refresh_token.get_secret_value(), client.client_id)

    bearer = {"authorization": f"Bearer {tokens.access_token.get_secret_value()}"}
    with pytest.raises(Unauthenticated, match="session revoked"):
        JwtProvider.for_token_issuer(oauth.tokens).authenticate(
            Credentials(transport="http", headers=bearer)
        )


def test_a_confidential_client_proves_itself_with_its_secret_shown_once():
    oauth = server()
    registered = oauth.register([CALLBACK], confidential=True)
    assert registered.secret and registered.secret not in registered.client.model_dump_json()
    code = signed_in(oauth, registered.client.client_id)

    with pytest.raises(OAuthError) as wrong:
        oauth.exchange_code(code, VERIFIER, registered.client.client_id, CALLBACK, "not-it")
    assert wrong.value.status == 401 and wrong.value.answer()["error"] == "invalid_client"


@pytest.mark.parametrize(
    ("uri", "accepted"),
    [
        (CALLBACK, True),
        ("http://127.0.0.1:8765/callback", True),
        ("http://localhost/callback", True),
        ("com.colegio.app:/oauth", True),
        ("http://evil.example/callback", False),
        (CALLBACK + "#fragment", False),
        ("myapp:/callback", False),
    ],
)
def test_only_a_redirect_safe_to_answer_may_be_registered(uri: str, accepted: bool):
    oauth = server()
    if accepted:
        assert oauth.register([uri]).client.redirect_uris == (uri,)
    else:
        with refused(OAuthErrorCode.INVALID_REDIRECT_URI):
            oauth.register([uri])


def test_metadata_publishes_only_the_capabilities_a_server_composes():
    endpoints = {
        "authorization": f"{ISSUER}/authorize",
        "token": f"{ISSUER}/token",
        "registration": f"{ISSUER}/register",
        "revocation": f"{ISSUER}/revoke",
    }

    class CodeOnly(AuthorizationCodeGrant, RefreshTokenGrant, AuthorizationServerMetadata):
        """No dynamic registration, no revocation endpoint."""

    whole = server().metadata(endpoints)
    narrow = CodeOnly(ISSUER, tokens=server().tokens, clients=InMemoryClients()).metadata(
        endpoints
    )

    assert whole["registration_endpoint"] and whole["revocation_endpoint"]
    assert "registration_endpoint" not in narrow and "revocation_endpoint" not in narrow
    assert narrow["grant_types_supported"] == ["authorization_code", "refresh_token"]
    assert (
        narrow["code_challenge_methods_supported"] == ["S256"] and narrow["issuer"] == ISSUER
    )
