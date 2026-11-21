"""Opaque tokens asked of the server that issued them (RFC 7662): cached by digest and never past
their own expiry, an inactive one refused and remembered briefly, a JWT left to `JwtProvider`,
the endpoint refusing this service's client a 503 — never a 401 to the caller — and the client
authenticating with a private key (RFC 7523)."""

import time
from datetime import UTC, datetime, timedelta

import pytest

from sincpro_framework.auth import (
    AuthProvider,
    Credentials,
    EndpointUnavailable,
    HttpRequest,
    PrivateKey,
    PublicKey,
)
from sincpro_framework.auth.client import ClientSecretBasic, PrivateKeyJwt
from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.infrastructure import jws
from sincpro_framework.auth.server import ClaimMapping, IntrospectionProvider
from sincpro_framework.data_layer.caching import Cache
from sincpro_framework.data_layer.store import InMemoryKeyValue
from sincpro_framework.runtime.testing import AuthProviderContract, ManualClock, ScriptedHttp

ISSUER = "https://auth.acme.bo/realms/acme"
ENDPOINT = f"{ISSUER}/protocol/openid-connect/token/introspect"


class Clock:
    def __init__(self) -> None:
        self.now = time.time()

    def __call__(self) -> float:
        return self.now


def active(request: HttpRequest) -> dict:
    return {
        "active": True,
        "iss": ISSUER,
        "aud": "billing-api",
        "sub": "4c1d",
        "exp": time.time() + 300,
        "scope": "billing:read",
        "client_id": "reports-app",
    }


def introspection(http: ScriptedHttp, **options) -> IntrospectionProvider:
    return IntrospectionProvider(
        client=ClientSecretBasic("billing-api", "s3cret"),
        endpoint=ENDPOINT,
        issuer=ISSUER,
        audience="billing-api",
        http=http,
        **options,
    )


def bearer(token: str) -> Credentials:
    return Credentials(transport="http", headers={"authorization": f"Bearer {token}"})


def test_an_active_token_is_the_subject_the_server_names_asked_once() -> None:
    http = ScriptedHttp({f"POST {ENDPOINT}": lambda r: ScriptedHttp.answer(active(r))})
    provider = introspection(http)
    identity = provider.authenticate(bearer("opaque-1"))
    again = provider.authenticate(bearer("opaque-1"))
    assert identity == again and identity is not None
    assert (identity.subject, identity.scopes, identity.client) == (
        "user:4c1d",
        {"billing:read"},
        "reports-app",
    )
    assert http.count(ENDPOINT) == 1
    assert b"token=opaque-1" in (http.sent[0].body or b"")
    assert http.sent[0].headers["authorization"].startswith("Basic ")


def test_the_cache_holds_the_tokens_digest_never_the_token() -> None:
    store = InMemoryKeyValue()
    http = ScriptedHttp({f"POST {ENDPOINT}": lambda r: ScriptedHttp.answer(active(r))})
    introspection(http, cache=Cache(store)).authenticate(bearer("opaque-secret-value"))
    held = " ".join(store._values) + " ".join(
        str(value) for value, _ in store._values.values()
    )
    assert held and "opaque-secret-value" not in held


def test_an_inactive_token_is_refused_and_asked_again_after_a_while() -> None:
    clock = Clock()
    http = ScriptedHttp({f"POST {ENDPOINT}": {"active": False}})
    provider = introspection(http, clock=clock, inactive_for=timedelta(seconds=10))
    for _ in range(3):
        with pytest.raises(exceptions.Unauthenticated, match="not active"):
            provider.authenticate(bearer("revoked"))
    assert http.count(ENDPOINT) == 1
    clock.now += 11
    with pytest.raises(exceptions.Unauthenticated):
        provider.authenticate(bearer("revoked"))
    assert http.count(ENDPOINT) == 2


def test_a_cached_answer_never_outlives_the_token() -> None:
    clock = ManualClock(datetime.now(UTC))
    http = ScriptedHttp(
        {
            f"POST {ENDPOINT}": {
                "active": True,
                "sub": "4c1d",
                "aud": "billing-api",
                "exp": time.time() + 30,
            }
        }
    )
    later = [time.time()]
    provider = introspection(http, cache=Cache(now=clock.now), clock=lambda: later[0])
    assert provider.authenticate(bearer("short")) is not None
    later[0] += 31
    with pytest.raises(exceptions.Unauthenticated, match="expired"):
        provider.authenticate(bearer("short"))


def test_another_audience_is_refused_and_a_jwt_left_to_its_provider() -> None:
    http = ScriptedHttp(
        {f"POST {ENDPOINT}": {"active": True, "sub": "x", "aud": "other-api"}}
    )
    provider = introspection(http)
    with pytest.raises(exceptions.Unauthenticated, match="another audience"):
        provider.authenticate(bearer("opaque-2"))
    assert provider.authenticate(bearer("eyJh.eyJz.c2ln")) is None


def test_the_endpoint_refusing_this_service_is_unavailable_never_the_callers_fault() -> None:
    http = ScriptedHttp(
        {f"POST {ENDPOINT}": ScriptedHttp.answer({"error": "invalid_client"}, status=401)}
    )
    with pytest.raises(EndpointUnavailable, match="refused this service's client"):
        introspection(http).authenticate(bearer("opaque-3"))
    down = ScriptedHttp(
        {f"POST {ENDPOINT}": EndpointUnavailable(ENDPOINT, "could not be reached")}
    )
    with pytest.raises(EndpointUnavailable):
        introspection(down).authenticate(bearer("opaque-3"))


def test_the_endpoint_is_discovered_on_the_first_token() -> None:
    http = ScriptedHttp(
        {
            f"{ISSUER}/.well-known/openid-configuration": {
                "issuer": ISSUER,
                "introspection_endpoint": ENDPOINT,
            },
            f"POST {ENDPOINT}": lambda r: ScriptedHttp.answer(active(r)),
        }
    )
    provider = IntrospectionProvider(
        client=ClientSecretBasic("billing-api", "s"),
        issuer=ISSUER,
        http=http,
        mapping=ClaimMapping.keycloak(),
    )
    assert http.sent == []
    assert provider.authenticate(bearer("opaque-4")) is not None


def test_a_private_key_assertion_is_signed_for_the_endpoint() -> None:
    key = PrivateKey.generate("RS256", key_id="client-1")
    fields, headers = PrivateKeyJwt("billing-api", key).applied([("token", "t")], ENDPOINT)
    named = dict(fields)
    assertion = jws.parse(named["client_assertion"])
    public = PublicKey.from_jwk(key.jwk(), "RS256")
    assert public.verify(assertion.signing_input, assertion.signature)
    assert (assertion.header["kid"], assertion.claims["aud"], assertion.claims["iss"]) == (
        "client-1",
        ENDPOINT,
        "billing-api",
    )
    assert headers == {} and "PRIVATE" not in repr(PrivateKeyJwt("billing-api", key))


class TestIntrospectionContract(AuthProviderContract):
    def make_provider(self) -> AuthProvider:
        http = ScriptedHttp(
            {
                f"POST {ENDPOINT}": lambda r: ScriptedHttp.answer(
                    active(r) if b"token=good" in (r.body or b"") else {"active": False}
                )
            }
        )
        return introspection(http)

    def accepted(self) -> Credentials:
        return bearer("good")

    def foreign(self) -> Credentials:
        return Credentials(transport="http", headers={"x-api-key": "k"})

    def rejected(self) -> Credentials:
        return bearer("bad")

    def make_provider_whose_backend_is_down(self) -> AuthProvider | None:
        return introspection(
            ScriptedHttp({f"POST {ENDPOINT}": EndpointUnavailable(ENDPOINT, "down")})
        )
