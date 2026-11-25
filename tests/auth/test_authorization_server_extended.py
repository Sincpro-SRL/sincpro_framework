"""The authorization server a project extends in its application layer: the resources it serves
agreeing with its verifier (#335), what a session keeps server-side (#336), how long a session
lives (#337), clients identified by their metadata document (#338), tenants (#339), and the RFC
details of #340 — each decision a policy or a public method, asked with the tenant.
"""

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
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
    canonical_resource,
)
from sincpro_framework.auth.accounts import (
    InMemoryRefreshTokens,
    InMemorySessionAttributes,
    SessionAttributes,
    SessionLifetime,
    SigningKeys,
    TokenIssuer,
)
from sincpro_framework.auth.server import (
    AuthorizationRequest,
    AuthorizationServer,
    ClientRegistries,
    InMemoryOAuthClients,
    JwtProvider,
    MetadataDocumentClients,
)
from sincpro_framework.data_layer.store import InMemoryKeyValue
from sincpro_framework.runtime.testing import ScriptedHttp, SessionAttributesContract

ISSUER = "https://mcp.sincpro.com.bo"
MCP = "https://mcp.sincpro.com.bo/mcp"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"
VERIFIER = "a-verifier-of-forty-three-characters-or-more-0123"
SECRET = "a token secret of thirty-two bytes!"
ANA = Identity.user("user:ana", tenant="acme").model_copy(
    update={"claims": {"credential": "cr_acme_ana", "agents": ["sales"]}}
)


class Clock:
    def __init__(self) -> None:
        self.now = 1_900_000_000.0

    def __call__(self) -> float:
        return self.now

    def moment(self) -> datetime:
        return datetime.fromtimestamp(self.now, UTC)


def challenge_of(verifier: str) -> str:
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )


def token_issuer(clock: Clock | None = None, cls=TokenIssuer, **options) -> TokenIssuer:
    clock = clock or Clock()
    stores = {
        "refresh_tokens": InMemoryRefreshTokens(now=clock.moment),
        "revocations": InMemoryRevocations(now=clock.moment),
    }
    settings = {"audience": "sincpro-odoo-mcp", **stores, **options}
    return cls(ISSUER, keys=SigningKeys.hmac({"2026-11": SECRET}), clock=clock, **settings)


def server(issuer: TokenIssuer | None = None, **options) -> AuthorizationServer:
    return AuthorizationServer(
        ISSUER,
        token_issuer=issuer or token_issuer(),
        client_registry=options.pop("client_registry", InMemoryOAuthClients()),
        store=options.pop("store", InMemoryKeyValue()),
        **options,
    )


def request(client_id: str, **fields) -> AuthorizationRequest:
    return AuthorizationRequest(
        client_id=client_id,
        redirect_uri=CALLBACK,
        code_challenge=challenge_of(VERIFIER),
        state="xyz",
        **fields,
    )


def sign_in(
    oauth: AuthorizationServer, client_id: str, who: Identity = ANA, **fields
) -> dict:
    pending = oauth.authorize(request(client_id, **fields))
    code = parse_qs(urlsplit(oauth.complete(pending.id, who)).query)["code"][0]
    return oauth.token(
        {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": VERIFIER,
            "redirect_uri": CALLBACK,
            "client_id": client_id,
        }
    ).answer()


def refresh(oauth: AuthorizationServer, client_id: str, tokens: dict, **form) -> dict:
    return oauth.token(
        {
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": client_id,
            **form,
        }
    ).answer()


def verify(issuer: TokenIssuer, tokens: dict) -> Identity:
    identity = JwtProvider.for_token_issuer(issuer).authenticate(
        Credentials(
            transport="http", headers={"authorization": f"Bearer {tokens['access_token']}"}
        )
    )
    assert identity is not None
    return identity


def claims_of(tokens: dict) -> dict:
    payload = tokens["access_token"].split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def refused(error: OAuthErrorCode) -> pytest.RaisesExc[OAuthError]:
    return pytest.raises(OAuthError, match=f"^{error.value}")


# --- #335 RFC 8707: the resources the server serves are the audiences its verifier accepts ----


@pytest.mark.parametrize(
    "asked", [MCP, "https://MCP.sincpro.com.bo:443/mcp/", "HTTPS://mcp.sincpro.com.bo/mcp"]
)
def test_a_token_for_a_served_resource_is_accepted_by_the_issuers_own_verifier(asked: str):
    issuer = token_issuer(resources=Resources(MCP))
    oauth = server(issuer)
    client = oauth.register([CALLBACK]).client

    tokens = sign_in(oauth, client.client_id, resource=asked)

    assert claims_of(tokens)["aud"] == MCP
    assert verify(issuer, tokens).subject == ANA.subject
    assert verify(issuer, refresh(oauth, client.client_id, tokens)).subject == ANA.subject


def test_a_resource_not_served_is_invalid_target_at_the_clients_redirect():
    oauth = server(token_issuer(resources=Resources(MCP)))
    client = oauth.register([CALLBACK]).client

    with refused(OAuthErrorCode.INVALID_TARGET) as raised:
        oauth.authorize(request(client.client_id, resource="https://evil.example/api"))

    assert raised.value.redirect_uri is not None
    assert raised.value.redirect_uri.startswith(CALLBACK)


def test_with_no_resources_only_the_issuers_own_audience_is_served():
    issuer = token_issuer(audience=MCP)
    oauth = server(issuer)
    client = oauth.register([CALLBACK]).client

    tokens = sign_in(oauth, client.client_id, resource=f"{MCP}/")

    assert verify(issuer, tokens).subject == ANA.subject
    with refused(OAuthErrorCode.INVALID_TARGET):
        oauth.authorize(
            request(client.client_id, resource="https://other.sincpro.com.bo/mcp")
        )


class TenantResources(ResourcePolicy):
    """One MCP resource per tenant — decided, never listed."""

    def audience_for(self, resource, client_id, tenant):
        canonical = canonical_resource(resource)
        return canonical if canonical == f"https://{tenant}.mcp.sincpro.com.bo/mcp" else None

    def serves(self, audience):
        host = urlsplit(audience).hostname or ""
        return host.endswith(".mcp.sincpro.com.bo")


def test_a_policy_of_the_project_decides_the_resource_per_tenant():
    issuer = token_issuer(resources=TenantResources())
    oauth = server(issuer)
    client = oauth.register([CALLBACK]).client

    tokens = sign_in(
        oauth, client.client_id, resource="https://acme.mcp.sincpro.com.bo/mcp", tenant="acme"
    )

    assert verify(issuer, tokens).tenant == "acme"
    with refused(OAuthErrorCode.INVALID_TARGET):
        oauth.authorize(
            request(
                client.client_id,
                resource="https://other.mcp.sincpro.com.bo/mcp",
                tenant="acme",
            )
        )


# --- #336 what a session keeps server-side -------------------------------------------------


def test_the_sign_ins_claims_are_kept_server_side_and_back_on_every_call_and_refresh():
    issuer = token_issuer(session_attributes=InMemorySessionAttributes())
    oauth = server(issuer)
    client = oauth.register([CALLBACK]).client

    tokens = sign_in(oauth, client.client_id)
    renewed = refresh(oauth, client.client_id, tokens)

    assert "cr_acme_ana" not in json.dumps(claims_of(tokens))
    for issued in (tokens, renewed):
        identity = verify(issuer, issued)
        assert identity.claims["credential"] == "cr_acme_ana"
        assert identity.claims["sub"] == ANA.subject  # the token's own claims still there


def test_a_revoked_session_keeps_nothing_and_its_tokens_are_refused():
    issuer = token_issuer(session_attributes=InMemorySessionAttributes())
    oauth = server(issuer)
    client = oauth.register([CALLBACK]).client
    tokens = sign_in(oauth, client.client_id)

    oauth.revoke(tokens["refresh_token"], client.client_id)

    with pytest.raises(Unauthenticated):
        verify(issuer, tokens)
    with refused(OAuthErrorCode.INVALID_GRANT):
        refresh(oauth, client.client_id, tokens)


def test_a_token_whose_session_kept_nothing_is_refused():
    attributes = InMemorySessionAttributes()
    issuer = token_issuer(session_attributes=attributes)
    tokens = issuer.issue(ANA).answer()
    attributes.delete(claims_of(tokens)["sid"])

    with pytest.raises(Unauthenticated, match="the session ended"):
        verify(issuer, tokens)


class KeepsOnlyTheCredential(TokenIssuer):
    def kept_claims(self, identity):
        return {"credential": identity.claims["credential"]}


def test_what_a_session_keeps_is_the_projects_decision():
    issuer = token_issuer(
        cls=KeepsOnlyTheCredential, session_attributes=InMemorySessionAttributes()
    )

    identity = verify(issuer, issuer.issue(ANA).answer())

    assert identity.claims["credential"] == "cr_acme_ana" and "agents" not in identity.claims


def test_resolvers_see_the_session_when_tokens_are_issued_and_refreshed():
    seen: list[str | None] = []

    class Records:
        def resolve(self, identity):
            seen.append(identity.session)
            return identity

    issuer = token_issuer(resolvers=[Records()])
    tokens = issuer.issue(ANA)
    assert tokens.refresh_token is not None
    issuer.refresh(tokens.refresh_token.get_secret_value())

    assert seen == [tokens.session, tokens.session]


class TestInMemorySessionAttributes(SessionAttributesContract):
    def make_store(self) -> SessionAttributes:
        return InMemorySessionAttributes()


# --- #337 how long a session lives ----------------------------------------------------------


def test_with_no_session_ttl_a_session_lives_while_it_is_used():
    clock = Clock()
    issuer = token_issuer(clock, idle_ttl=timedelta(days=30), session_ttl=None)
    tokens = issuer.issue(ANA)

    for _ in range(12):  # a year, refreshed every month
        clock.now += timedelta(days=29).total_seconds()
        assert tokens.refresh_token is not None
        tokens = issuer.refresh(tokens.refresh_token.get_secret_value())

    clock.now += timedelta(days=31).total_seconds()
    with pytest.raises(Unauthenticated):
        assert tokens.refresh_token is not None
        issuer.refresh(tokens.refresh_token.get_secret_value())


class PerTenant(TokenIssuer):
    def lifetime_for(self, identity, client):
        if identity.tenant == "acme":
            return SessionLifetime(idle=timedelta(hours=1), at_most=timedelta(hours=2))
        return super().lifetime_for(identity, client)


def test_the_lifetime_chosen_per_tenant_is_honoured_by_every_refresh():
    clock = Clock()
    issuer = token_issuer(clock, cls=PerTenant, idle_ttl=timedelta(days=30))
    acme = issuer.issue(ANA)
    other = issuer.issue(Identity.user("user:luis", tenant="globex"))

    clock.now += timedelta(minutes=50).total_seconds()
    assert acme.refresh_token is not None and other.refresh_token is not None
    acme = issuer.refresh(acme.refresh_token.get_secret_value())
    clock.now += timedelta(minutes=61).total_seconds()

    with pytest.raises(Unauthenticated):
        assert acme.refresh_token is not None
        issuer.refresh(acme.refresh_token.get_secret_value())
    assert issuer.refresh(other.refresh_token.get_secret_value()).session == other.session


def test_a_revoked_session_with_no_absolute_end_stays_revoked_while_its_tokens_live():
    clock = Clock()
    issuer = token_issuer(clock, idle_ttl=timedelta(days=30), session_ttl=None)
    tokens = issuer.issue(ANA)
    issuer.revoke_session(tokens.session, ANA.subject)

    clock.now += timedelta(days=29).total_seconds()

    with pytest.raises(Unauthenticated):
        assert tokens.refresh_token is not None
        issuer.refresh(tokens.refresh_token.get_secret_value())


# --- #338 Client ID Metadata Documents ------------------------------------------------------

DOCUMENT_URL = "https://claude.ai/oauth/mcp-client.json"


def metadata_document(**fields) -> dict:
    return {
        "client_id": DOCUMENT_URL,
        "client_name": "Claude",
        "redirect_uris": [CALLBACK],
        "token_endpoint_auth_method": "none",
        **fields,
    }


def test_a_client_identified_by_its_metadata_document_signs_in_with_no_registration():
    http = ScriptedHttp({DOCUMENT_URL: ScriptedHttp.answer(metadata_document())})
    clients = ClientRegistries(
        InMemoryOAuthClients(), MetadataDocumentClients(http=http, store=InMemoryKeyValue())
    )
    issuer = token_issuer()
    oauth = server(issuer, client_registry=clients)

    tokens = sign_in(oauth, DOCUMENT_URL)

    assert verify(issuer, tokens).subject == ANA.subject
    assert len(http.sent) == 1  # fetched once, cached for /token
    assert oauth.metadata({})["client_id_metadata_document_supported"] is True


@pytest.mark.parametrize(
    "document",
    [
        metadata_document(client_id="https://claude.ai/another.json"),
        metadata_document(redirect_uris=["https://claude.ai/cb#fragment"]),
        metadata_document(redirect_uris=[]),
        metadata_document(token_endpoint_auth_method="client_secret_basic"),
    ],
)
def test_a_metadata_document_that_does_not_describe_a_safe_public_client_is_unknown(document):
    http = ScriptedHttp({DOCUMENT_URL: ScriptedHttp.answer(document)})
    oauth = server(
        client_registry=MetadataDocumentClients(http=http, store=InMemoryKeyValue())
    )

    with refused(OAuthErrorCode.INVALID_CLIENT):
        oauth.authorize(request(DOCUMENT_URL))


@pytest.mark.parametrize(
    "client_id",
    [
        "https://127.0.0.1/client.json",
        "https://localhost/client.json",
        "http://claude.ai/client.json",
        "https://claude.ai",
        "https://user@claude.ai/client.json",
    ],
)
def test_a_client_id_that_is_not_a_fetchable_document_url_is_never_fetched(client_id: str):
    http = ScriptedHttp()
    clients = MetadataDocumentClients(http=http, store=InMemoryKeyValue())

    assert clients.find(client_id) is None
    assert http.sent == []


def test_a_server_without_metadata_documents_does_not_announce_them():
    assert "client_id_metadata_document_supported" not in server().metadata({})


# --- #339 tenants ----------------------------------------------------------------------------


def test_a_client_bound_to_a_tenant_serves_only_its_tenant():
    oauth = server()
    client = oauth.register([CALLBACK], tenant="acme").client

    pending = oauth.authorize(request(client.client_id))
    assert pending.request.tenant == "acme"  # inherited from the client

    with refused(OAuthErrorCode.INVALID_REQUEST):
        oauth.authorize(request(client.client_id, tenant="globex"))


def test_a_sign_in_of_another_tenant_is_access_denied_at_the_clients_redirect():
    oauth = server()
    client = oauth.register([CALLBACK]).client
    pending = oauth.authorize(request(client.client_id, tenant="globex"))

    with refused(OAuthErrorCode.ACCESS_DENIED) as raised:
        oauth.complete(pending.id, ANA)

    assert raised.value.redirect_uri is not None
    assert "error=access_denied" in raised.value.redirect_uri
    assert oauth.find_pending(pending.id) is None  # spent


def test_a_client_registers_for_the_tenant_the_transport_names():
    oauth = server()

    registered = oauth.register_from_metadata({"redirect_uris": [CALLBACK]}, tenant="acme")

    assert registered.client.tenant == "acme"


# --- #340 store, PKCE method, registered method, narrowed scope ------------------------------


def test_the_store_given_holds_pending_authorizations_and_codes():
    store = InMemoryKeyValue()
    oauth = server(store=store)
    client = oauth.register([CALLBACK]).client

    pending = oauth.authorize(request(client.client_id))

    other_replica = server(
        oauth.token_issuer, client_registry=oauth.client_registry, store=store
    )
    assert other_replica.find_pending(pending.id) is not None


def test_an_omitted_challenge_method_is_plain_and_refused_at_authorize():
    oauth = server()
    client = oauth.register([CALLBACK]).client
    query = {
        "client_id": client.client_id,
        "redirect_uri": CALLBACK,
        "response_type": "code",
        "code_challenge": VERIFIER,
    }

    with refused(OAuthErrorCode.INVALID_REQUEST) as raised:
        oauth.authorize(AuthorizationRequest.from_query(query))

    assert "plain" in raised.value.description


def test_a_request_from_its_query_reads_every_parameter():
    asked = AuthorizationRequest.from_query(
        {
            "client_id": "cl_1",
            "redirect_uri": CALLBACK,
            "code_challenge": "c",
            "code_challenge_method": "S256",
            "scope": "read write",
            "state": "s",
            "resource": MCP,
        },
        tenant="acme",
    )

    assert asked.scopes == frozenset({"read", "write"}) and asked.tenant == "acme"
    with refused(OAuthErrorCode.INVALID_REQUEST):
        AuthorizationRequest.from_query({"client_id": "cl_1"})


def test_a_client_is_answered_the_method_it_registered_with():
    oauth = server()

    post = oauth.register_from_metadata(
        {"redirect_uris": [CALLBACK], "token_endpoint_auth_method": "client_secret_post"}
    ).answer()
    omitted = oauth.register_from_metadata({"redirect_uris": [CALLBACK]}).answer()

    assert (
        post["token_endpoint_auth_method"] == "client_secret_post" and post["client_secret"]
    )
    assert omitted["token_endpoint_auth_method"] == "none" and "client_secret" not in omitted


def test_a_refresh_may_narrow_the_scope_never_widen_it():
    oauth = server()
    client = oauth.register([CALLBACK]).client
    tokens = sign_in(oauth, client.client_id, scopes=frozenset({"read", "write"}))

    narrowed = refresh(oauth, client.client_id, tokens, scope="read")
    again = refresh(oauth, client.client_id, narrowed)

    assert narrowed["scope"] == "read"
    assert again["scope"] == "read write"  # the refresh token keeps the whole grant
    with refused(OAuthErrorCode.INVALID_SCOPE):
        refresh(oauth, client.client_id, again, scope="admin")
