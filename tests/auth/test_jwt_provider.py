"""A bearer JWT verified against the issuers a service trusts: routed by its issuer, checked
against that issuer's keys, algorithms and audience — never the token's say — mapped the way
Keycloak, Entra and Google write their claims, refused when revoked, resolved further, and
refused the same way on the wires."""

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.auth import (
    AccessControl,
    AuthProvider,
    ClaimMapping,
    Credentials,
    IdentityKind,
    IdentityMiddleware,
    InMemoryRevocations,
    Issuer,
    JwtProvider,
    Permission,
    ProtectedResource,
    RolePermissions,
    SharedSecrets,
    security,
)
from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.infrastructure import jws
from sincpro_framework.runtime.testing import AuthProviderContract

KEYCLOAK = "https://auth.acme.bo/realms/acme"
SECRET = "a shared secret of thirty-two by!"
OTHER_SECRET = "another shared secret, 32 bytes!"
NOW = time.time()


def mint(
    claims: dict[str, Any],
    secret: str = SECRET,
    kid: str | None = "2026-09",
    alg: str = "HS256",
    header: dict[str, Any] | None = None,
) -> str:
    head = {"alg": alg, "typ": "JWT", **({"kid": kid} if kid else {}), **(header or {})}
    signing = jws.HmacKey.of(kid, secret, alg if alg in jws.HMAC_DIGESTS else "HS256").sign
    return jws.encode(head, claims, signing)


def claims(**changed: Any) -> dict[str, Any]:
    return {
        "iss": KEYCLOAK,
        "aud": ["billing-api", "account"],
        "sub": "4c1d",
        "exp": NOW + 300,
        "iat": NOW,
        "auth_time": NOW - 60,
        "jti": "jti-1",
        "sid": "sid-1",
        "azp": "billing-web",
        "typ": "Bearer",
        "acr": "1",
        "amr": ["pwd", "otp"],
        "scope": "openid billing:read",
        "realm_access": {"roles": ["app:billing", "offline_access"]},
        "resource_access": {"billing-api": {"roles": ["accountant"]}},
        **changed,
    }


def issuer(**options: Any) -> Issuer:
    return Issuer(
        KEYCLOAK,
        audience="billing-api",
        keys=SharedSecrets({"2026-09": SECRET}),
        mapping=ClaimMapping.keycloak(client="billing-api"),
        **options,
    )


def bearer(token: str) -> Credentials:
    return Credentials(transport="http", headers={"authorization": f"Bearer {token}"})


def test_a_keycloak_token_is_read_as_the_person_it_names() -> None:
    identity = JwtProvider(issuer()).authenticate(bearer(mint(claims())))
    assert identity is not None
    assert (identity.subject, identity.kind, identity.provider) == (
        "user:4c1d",
        IdentityKind.USER,
        "jwt",
    )
    assert identity.roles == {"app:billing", "offline_access", "accountant"}
    assert identity.scopes == {"openid", "billing:read"}
    assert (identity.client, identity.session, identity.token_id) == (
        "billing-web",
        "sid-1",
        "jti-1",
    )
    assert identity.assurance.level == "1" and identity.assurance.methods == {"pwd", "otp"}
    assert identity.expires_at == datetime.fromtimestamp(NOW + 300, UTC)
    assert identity.claims["realm_access"] == {"roles": ["app:billing", "offline_access"]}


def test_a_keycloak_service_account_is_a_service() -> None:
    token = mint(claims(sub="svc-7", preferred_username="service-account-reports"))
    identity = JwtProvider(issuer()).authenticate(bearer(token))
    assert identity is not None and identity.kind == IdentityKind.SERVICE
    assert identity.subject == "service:svc-7"


def test_entra_names_the_person_by_object_id_and_the_tenant_by_its_id() -> None:
    entra = Issuer(
        "https://login.microsoftonline.com/t-1/v2.0",
        audience="api://billing",
        keys=SharedSecrets({"k": SECRET}),
        mapping=ClaimMapping.entra(),
    )
    token = mint(
        {
            "iss": "https://login.microsoftonline.com/t-1/v2.0",
            "aud": "api://billing",
            "sub": "pairwise",
            "oid": "obj-9",
            "tid": "t-1",
            "exp": NOW + 60,
            "scp": "Invoices.Read",
            "roles": ["Billing.Admin"],
            "idtyp": "app",
        },
        kid="k",
    )
    identity = JwtProvider(entra).authenticate(bearer(token))
    assert identity is not None
    assert (identity.subject, identity.tenant, identity.kind) == (
        "service:obj-9",
        "t-1",
        IdentityKind.SERVICE,
    )
    assert identity.roles == {"Billing.Admin"} and identity.scopes == {"Invoices.Read"}


@pytest.mark.parametrize(
    ("token", "reason"),
    [
        (mint(claims())[:-3] + "AAA", "signature does not match"),
        (mint(claims(), alg="none", kid="2026-09").rsplit(".", 1)[0] + ".", "none"),
        (mint(claims(), alg="HS512"), "HS512"),
        (mint(claims(), kid="rotated-out"), "unknown key"),
        (mint(claims(), secret=OTHER_SECRET), "signature does not match"),
        (mint(claims(aud="another-api")), "another audience"),
        (mint(claims(exp=NOW - 61)), "expired"),
        (mint(claims(nbf=NOW + 120)), "not valid yet"),
        (mint(claims(iat=NOW + 120)), "issued in the future"),
        (mint(claims(typ="ID")), "not an access token"),
        (mint(claims(typ="Refresh")), "not an access token"),
        (mint(claims(), header={"crit": ["exp"]}), "critical header"),
        (mint(claims(sub=None)), "lacks sub"),
    ],
)
def test_a_trusted_issuers_token_that_fails_is_refused_saying_why(
    token: str, reason: str
) -> None:
    with pytest.raises(exceptions.Unauthenticated, match=reason):
        JwtProvider(issuer()).authenticate(bearer(token))


def test_a_key_named_in_the_token_is_never_used() -> None:
    token = mint(
        claims(),
        secret=OTHER_SECRET,
        header={"jwk": {"kty": "oct", "k": "x"}, "jku": "https://evil"},
    )
    with pytest.raises(exceptions.Unauthenticated, match="signature does not match"):
        JwtProvider(issuer()).authenticate(bearer(token))


def test_what_is_not_its_kind_is_left_to_the_next_provider() -> None:
    provider = JwtProvider(issuer())
    assert provider.authenticate(Credentials(transport="http")) is None
    assert provider.authenticate(bearer("rt_an-opaque-token")) is None
    assert provider.authenticate(bearer(mint(claims(iss="https://elsewhere")))) is None


def test_each_issuer_is_checked_against_its_own_keys() -> None:
    own = Issuer("billing", audience="billing", keys=SharedSecrets({"own": OTHER_SECRET}))
    provider = JwtProvider([issuer(), own])
    mine = mint(
        {"iss": "billing", "aud": "billing", "sub": "7", "exp": NOW + 60},
        secret=OTHER_SECRET,
        kid="own",
    )
    assert provider.authenticate(bearer(mine)) is not None
    assert provider.authenticate(bearer(mint(claims()))) is not None
    forged = mint(
        {"iss": "billing", "aud": "billing", "sub": "7", "exp": NOW + 60}, kid="2026-09"
    )
    with pytest.raises(exceptions.Unauthenticated):
        provider.authenticate(bearer(forged))


def test_an_issuer_that_cannot_be_trusted_is_a_programming_error() -> None:
    with pytest.raises(exceptions.ProgrammingError, match="no audience"):
        Issuer(KEYCLOAK, audience=[], keys=SharedSecrets({"k": SECRET}))
    with pytest.raises(exceptions.ProgrammingError):
        Issuer(
            KEYCLOAK,
            audience="a",
            keys=SharedSecrets({"k": SECRET}),
            algorithms=frozenset({"RS256"}),
        )
    with pytest.raises(exceptions.ProgrammingError):
        Issuer(
            KEYCLOAK,
            audience="a",
            keys=SharedSecrets({"k": SECRET}),
            algorithms=frozenset({"none"}),
        )
    with pytest.raises(exceptions.ProgrammingError, match="twice"):
        JwtProvider([issuer(), issuer()])


def test_only_the_clients_allowed_are_accepted() -> None:
    provider = JwtProvider(issuer(authorized_parties=frozenset({"billing-web"})))
    assert provider.authenticate(bearer(mint(claims()))) is not None
    with pytest.raises(exceptions.Unauthenticated, match="client not accepted"):
        provider.authenticate(bearer(mint(claims(azp="someone-else"))))


def test_a_revoked_session_or_a_subject_signed_out_is_refused() -> None:
    revocations = InMemoryRevocations()
    provider = JwtProvider(issuer(), revocations=revocations)
    revocations.revoke_session("sid-1", datetime.now(UTC) + timedelta(hours=1))
    with pytest.raises(exceptions.Unauthenticated, match="session revoked"):
        provider.authenticate(bearer(mint(claims())))
    assert provider.authenticate(bearer(mint(claims(sid="sid-2")))) is not None
    revocations.revoke_subject("user:4c1d", datetime.now(UTC) + timedelta(hours=1))
    with pytest.raises(exceptions.Unauthenticated, match="signed out"):
        provider.authenticate(bearer(mint(claims(sid="sid-2", iat=NOW - 5))))


class Perm(Permission):
    ISSUE = "billing.invoice.issue"


def test_the_identity_goes_through_its_resolvers() -> None:
    provider = JwtProvider(
        issuer(), resolvers=[RolePermissions({"accountant": {Perm.ISSUE}})]
    )
    identity = provider.authenticate(bearer(mint(claims())))
    assert identity is not None and identity.permissions == {Perm.ISSUE}


def test_a_refusal_says_where_to_get_a_token() -> None:
    resource = ProtectedResource(
        "https://billing.acme.bo/mcp", authorization_servers=[KEYCLOAK]
    )
    provider = JwtProvider(issuer(), resource=resource, realm="billing")
    said = provider.challenge_for(exceptions.Unauthenticated('token "expired"'))
    assert said == (
        'Bearer realm="billing", error="invalid_token", error_description="token \'expired\'", '
        'resource_metadata="https://billing.acme.bo/.well-known/oauth-protected-resource/mcp"'
    )
    assert provider.challenge() == (
        'Bearer realm="billing", '
        'resource_metadata="https://billing.acme.bo/.well-known/oauth-protected-resource/mcp"'
    )
    assert resource.metadata() == {
        "resource": "https://billing.acme.bo/mcp",
        "authorization_servers": [KEYCLOAK],
        "bearer_methods_supported": ["header"],
    }


class TestJwtProviderContract(AuthProviderContract):
    def make_provider(self) -> AuthProvider:
        return JwtProvider(
            issuer(), resolvers=[RolePermissions({"accountant": {Perm.ISSUE}})]
        )

    def accepted(self) -> Credentials:
        return bearer(mint(claims()))

    def foreign(self) -> Credentials:
        return Credentials(transport="http", headers={"x-api-key": "sk_1"})

    def rejected(self) -> Credentials:
        return bearer(mint(claims(exp=NOW - 120)))

    def granted_permission(self) -> str | None:
        return Perm.ISSUE


class CommandIssue(DataTransferObject):
    total: int = 1


class Issued(DataTransferObject):
    by: str


def test_a_project_app_answers_an_expired_token_401_saying_why() -> None:
    auth = AccessControl[Perm](
        providers=[
            JwtProvider(issuer(), resolvers=[RolePermissions({"accountant": {Perm.ISSUE}})])
        ]
    )
    billing = UseFramework("jwt-billing", log_after_execution=False)

    @billing.feature(CommandIssue)
    @auth.requires(Perm.ISSUE)
    class Issue(Feature):
        def execute(self, dto: CommandIssue) -> Issued:
            return Issued(by=security.identity.subject)

    auth.on(billing)

    async def issue(request: Request) -> JSONResponse:
        answer = await asyncio.to_thread(billing, CommandIssue(), Issued)
        assert answer is not None
        return JSONResponse({"by": answer.by})

    app = Starlette(routes=[Route("/invoices", issue, methods=["POST"])])
    app.add_middleware(IdentityMiddleware, access=auth)
    client = TestClient(app)
    ok = client.post("/invoices", headers={"authorization": f"Bearer {mint(claims())}"})
    assert ok.json() == {"by": "user:4c1d"}
    expired = client.post(
        "/invoices", headers={"authorization": f"Bearer {mint(claims(exp=NOW - 120))}"}
    )
    assert expired.status_code == 401
    assert 'error="invalid_token"' in expired.headers["www-authenticate"]
    assert expired.json()["error"]["reason"] == "token expired"
