"""An OAuth authorization server over the service's own token issuer: the code grant with PKCE as
its policy asks, a code used once, refresh bound to its client, revocation, dynamic registration
of safe redirects only, and metadata that publishes only the capabilities a server composes.
"""

import base64
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from sincpro_framework.auth import (
    Credentials,
    Identity,
    InMemoryRevocations,
    OAuthError,
    OAuthErrorCode,
    ResourcePolicy,
    Resources,
    Unauthenticated,
)
from sincpro_framework.auth.accounts import InMemoryRefreshTokens, SigningKeys, TokenIssuer
from sincpro_framework.auth.server import (
    AuthorizationCodeGrant,
    AuthorizationRequest,
    AuthorizationServer,
    AuthorizationServerMetadata,
    CodeChallengeMethod,
    InMemoryOAuthClients,
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


def server(resources: ResourcePolicy | None = None, **options) -> AuthorizationServer:
    tokens = TokenIssuer(
        ISSUER,
        audience="colegio",
        keys=SigningKeys.hmac({"2026-11": "a token secret of thirty-two bytes!"}),
        refresh_tokens=InMemoryRefreshTokens(),
        revocations=InMemoryRevocations(),
        resources=resources,
    )
    return AuthorizationServer(
        ISSUER, token_issuer=tokens, client_registry=InMemoryOAuthClients(), **options
    )


def authorization_request(client_id: str, **fields) -> AuthorizationRequest:
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
    pending = oauth.authorize(authorization_request(client_id, **fields))
    return code_from(oauth.complete(pending.id, ANA))


def refused(error: OAuthErrorCode) -> pytest.RaisesExc[OAuthError]:
    return pytest.raises(OAuthError, match=f"^{error.value}")


def test_a_client_registers_a_person_signs_in_and_the_code_becomes_tokens_the_api_accepts():
    oauth = server()
    client = oauth.register([CALLBACK], name="Claude").client
    pending = oauth.authorize(
        authorization_request(client.client_id, scopes=frozenset({"dues"}))
    )
    shown = oauth.find_pending(pending.id)
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
    identity = JwtProvider.for_token_issuer(oauth.token_issuer).authenticate(
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
        oauth.authorize(authorization_request("cl_nobody"))
    with refused(OAuthErrorCode.INVALID_REQUEST):
        oauth.authorize(
            authorization_request(client.client_id).model_copy(
                update={"redirect_uri": CALLBACK + "/"}
            )
        )


def test_pkce_is_required_of_every_client_by_default_and_s256_is_the_method():
    oauth = server()
    client = oauth.register([CALLBACK], confidential=True).client
    with refused(OAuthErrorCode.INVALID_REQUEST):
        oauth.authorize(authorization_request(client.client_id, code_challenge=""))
    with refused(OAuthErrorCode.INVALID_REQUEST):
        oauth.authorize(
            authorization_request(
                client.client_id, code_challenge_method=CodeChallengeMethod.PLAIN
            )
        )


def test_a_server_may_ask_pkce_of_public_clients_only_or_accept_plain():
    oauth = server(
        pkce_policy=PkcePolicy.PUBLIC_CLIENTS,
        challenge_methods=frozenset({CodeChallengeMethod.S256, CodeChallengeMethod.PLAIN}),
    )
    confidential = oauth.register([CALLBACK], confidential=True)
    public = oauth.register([CALLBACK]).client

    code = signed_in(oauth, confidential.client.client_id, code_challenge="")
    assert oauth.exchange_code(
        code, None, confidential.client.client_id, CALLBACK, confidential.secret
    )
    with refused(OAuthErrorCode.INVALID_REQUEST):
        oauth.authorize(authorization_request(public.client_id, code_challenge=""))
    code = signed_in(
        oauth,
        public.client_id,
        code_challenge=VERIFIER,
        code_challenge_method=CodeChallengeMethod.PLAIN,
    )
    assert oauth.exchange_code(code, VERIFIER, public.client_id, CALLBACK)


def test_with_pkce_optional_a_request_without_a_challenge_is_answered():
    oauth = server(pkce_policy=PkcePolicy.OPTIONAL)
    client = oauth.register([CALLBACK]).client
    code = signed_in(oauth, client.client_id, code_challenge="")
    assert oauth.exchange_code(code, None, client.client_id, CALLBACK)


def test_a_person_who_refuses_sends_the_client_access_denied_and_the_authorization_is_gone():
    oauth = server()
    client = oauth.register([CALLBACK]).client
    pending = oauth.authorize(authorization_request(client.client_id))

    answer = parse_qs(urlsplit(oauth.deny(pending.id)).query)

    assert answer["error"] == ["access_denied"] and answer["state"] == ["xyz"]
    assert oauth.find_pending(pending.id) is None
    with refused(OAuthErrorCode.INVALID_REQUEST):
        oauth.complete(pending.id, ANA)


def test_a_scope_the_client_may_not_ask_for_is_refused():
    oauth = server()
    client = oauth.register([CALLBACK], scopes={"dues"}).client
    with refused(OAuthErrorCode.INVALID_SCOPE):
        oauth.authorize(authorization_request(client.client_id, scopes=frozenset({"admin"})))


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
        JwtProvider.for_token_issuer(oauth.token_issuer).authenticate(
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
    narrow = CodeOnly(
        ISSUER, token_issuer=server().token_issuer, client_registry=InMemoryOAuthClients()
    ).metadata(endpoints)

    assert whole["registration_endpoint"] and whole["revocation_endpoint"]
    assert "registration_endpoint" not in narrow and "revocation_endpoint" not in narrow
    assert narrow["grant_types_supported"] == ["authorization_code", "refresh_token"]
    assert (
        narrow["code_challenge_methods_supported"] == ["S256"] and narrow["issuer"] == ISSUER
    )


def basic(client_id: str, secret: str) -> str:
    return "Basic " + base64.b64encode(f"{client_id}:{secret}".encode()).decode()


def test_the_token_endpoint_dispatches_by_grant_type_and_reads_client_secret_basic():
    oauth = server()
    registered = oauth.register([CALLBACK], confidential=True)
    client_id = registered.client.client_id
    assert registered.secret is not None
    form = {
        "grant_type": "authorization_code",
        "code": signed_in(oauth, client_id),
        "redirect_uri": CALLBACK,
        "code_verifier": VERIFIER,
    }

    tokens = oauth.token(form, basic(client_id, registered.secret))
    assert tokens.refresh_token is not None
    renewed = oauth.token(
        {
            "grant_type": "refresh_token",
            "refresh_token": tokens.refresh_token.get_secret_value(),
            "client_id": client_id,
            "client_secret": registered.secret,
        }
    )

    assert renewed.session == tokens.session
    with refused(OAuthErrorCode.UNSUPPORTED_GRANT_TYPE):
        oauth.token({"grant_type": "password", "client_id": client_id})
    with refused(OAuthErrorCode.INVALID_REQUEST):
        oauth.token({"grant_type": "authorization_code", "client_id": client_id})
    with refused(OAuthErrorCode.INVALID_CLIENT):
        oauth.token({**form, "code": "c"}, basic(client_id, "not-it"))


def test_a_client_revokes_only_its_own_tokens():
    oauth = server()
    client = oauth.register([CALLBACK]).client
    other = oauth.register([CALLBACK]).client
    tokens = oauth.exchange_code(
        signed_in(oauth, client.client_id), VERIFIER, client.client_id, CALLBACK
    )
    assert tokens.refresh_token is not None
    refresh = tokens.refresh_token.get_secret_value()

    oauth.revoke(refresh, other.client_id)
    oauth.revoke(tokens.access_token.get_secret_value(), other.client_id)

    assert oauth.refresh(refresh, client.client_id).session == tokens.session


def test_a_loopback_redirect_matches_whatever_port_the_app_listens_on():
    oauth = server()
    client = oauth.register(["http://127.0.0.1/callback"]).client

    code = signed_in(oauth, client.client_id, redirect_uri="http://127.0.0.1:53123/callback")

    assert oauth.exchange_code(
        code, VERIFIER, client.client_id, "http://127.0.0.1:53123/callback"
    )
    with refused(OAuthErrorCode.INVALID_REQUEST):
        oauth.authorize(
            authorization_request(client.client_id, redirect_uri="http://127.0.0.1:1/other")
        )


def test_a_request_refused_past_its_redirect_check_goes_back_to_the_client():
    oauth = server()
    client = oauth.register([CALLBACK], scopes={"dues"}).client

    for refusal, fields in (
        (OAuthErrorCode.INVALID_SCOPE, {"scopes": frozenset({"admin"})}),
        (OAuthErrorCode.UNSUPPORTED_RESPONSE_TYPE, {"response_type": "token"}),
        (OAuthErrorCode.INVALID_TARGET, {"resource": "not-a-uri"}),
    ):
        with refused(refusal) as raised:
            oauth.authorize(authorization_request(client.client_id, **fields))
        assert raised.value.redirect_uri is not None
        answer = parse_qs(urlsplit(raised.value.redirect_uri).query)
        assert raised.value.redirect_uri.startswith(CALLBACK)
        assert answer["error"] == [refusal.value] and answer["state"] == ["xyz"]
        assert answer["iss"] == [ISSUER]
    with refused(OAuthErrorCode.INVALID_CLIENT) as unknown:
        oauth.authorize(authorization_request("cl_nobody"))
    assert unknown.value.redirect_uri is None


def test_the_resource_asked_for_is_the_audience_of_every_token_of_the_session():
    resource = "https://api.colegio.bo/mcp"
    oauth = server(resources=Resources(resource))
    client = oauth.register([CALLBACK]).client

    tokens = oauth.exchange_code(
        signed_in(oauth, client.client_id, resource=resource),
        VERIFIER,
        client.client_id,
        CALLBACK,
    )
    assert tokens.refresh_token is not None
    renewed = oauth.refresh(tokens.refresh_token.get_secret_value(), client.client_id)

    for issued in (tokens, renewed):
        payload = issued.access_token.get_secret_value().split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        assert claims["aud"] == resource
        bearer = f"Bearer {issued.access_token.get_secret_value()}"
        verified = JwtProvider.for_token_issuer(oauth.token_issuer).authenticate(
            Credentials(transport="http", headers={"authorization": bearer})
        )
        assert verified is not None and verified.subject == ANA.subject


def test_a_code_presented_again_revokes_the_session_it_opened():
    oauth = server()
    client = oauth.register([CALLBACK]).client
    code = signed_in(oauth, client.client_id)
    tokens = oauth.exchange_code(code, VERIFIER, client.client_id, CALLBACK)

    with refused(OAuthErrorCode.INVALID_GRANT):
        oauth.exchange_code(code, VERIFIER, client.client_id, CALLBACK)

    bearer = {"authorization": f"Bearer {tokens.access_token.get_secret_value()}"}
    with pytest.raises(Unauthenticated, match="session revoked"):
        JwtProvider.for_token_issuer(oauth.token_issuer).authenticate(
            Credentials(transport="http", headers=bearer)
        )


def test_metadata_announces_neither_refresh_nor_revocation_the_issuer_cannot_serve():
    tokens = TokenIssuer(
        ISSUER,
        audience="colegio",
        keys=SigningKeys.hmac({"2026-11": "a token secret of thirty-two bytes!"}),
    )
    oauth = AuthorizationServer(
        ISSUER, token_issuer=tokens, client_registry=InMemoryOAuthClients()
    )

    document = oauth.metadata(
        {
            "token": f"{ISSUER}/token",
            "revocation": f"{ISSUER}/revoke",
            "jwks": f"{ISSUER}/jwks",
        }
    )

    assert document["grant_types_supported"] == ["authorization_code"]
    assert "revocation_endpoint" not in document
    assert "jwks_uri" not in document  # HMAC keys: nothing public to publish
    with refused(OAuthErrorCode.UNSUPPORTED_GRANT_TYPE):
        oauth.token({"grant_type": "refresh_token", "client_id": "cl_x"})


@pytest.mark.parametrize(
    "uri", ["https://user:pw@evil.example/cb", CALLBACK + "#", "https:///cb"]
)
def test_a_redirect_with_user_information_or_an_empty_fragment_is_never_registered(uri: str):
    with refused(OAuthErrorCode.INVALID_REDIRECT_URI):
        server().register([uri])


def test_registration_closed_by_an_initial_access_token_and_answered_as_rfc_7591():
    oauth = server(registration_token="iat-of-the-platform")
    metadata = {
        "redirect_uris": [CALLBACK],
        "client_name": "Claude",
        "grant_types": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_method": "client_secret_basic",
        "scope": "dues admin",
    }

    with refused(OAuthErrorCode.INVALID_TOKEN) as closed:
        oauth.register_from_metadata(metadata)
    answer = oauth.register_from_metadata(metadata, "iat-of-the-platform").answer()

    assert closed.value.status == 401
    assert answer["client_secret"] and answer["client_name"] == "Claude"
    assert answer["token_endpoint_auth_method"] == "client_secret_basic"
    assert answer["scope"] == "admin dues" and answer["client_id_issued_at"] > 0
    with refused(OAuthErrorCode.INVALID_CLIENT_METADATA):
        oauth.register_from_metadata(
            {**metadata, "grant_types": ["implicit"]}, "iat-of-the-platform"
        )
    with refused(OAuthErrorCode.INVALID_CLIENT_METADATA):
        oauth.register_from_metadata({"redirect_uris": CALLBACK}, "iat-of-the-platform")
