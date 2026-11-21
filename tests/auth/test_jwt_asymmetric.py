"""Asymmetric JWTs — the `[jwt]` extra: RS256, ES256 and EdDSA verified against an issuer's
published keys, found by discovery on the first token; a key rotated in is fetched once, a forged
key id never becomes a stream of fetches, the last good set stands in while the issuer is down,
and an HMAC token signed with the public key is refused."""

import time
from typing import Any

import pytest

from sincpro_framework.auth import Credentials, EndpointUnavailable, PrivateKey, PublicKey
from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.infrastructure import jws
from sincpro_framework.auth.server import (
    ClaimMapping,
    DpopProofs,
    Issuer,
    JwtProvider,
    PublicKeys,
)
from sincpro_framework.auth.server.adapters.bearer_tokens.jwks import Jwks
from sincpro_framework.runtime.testing import ScriptedHttp

ISSUER = "https://auth.acme.bo/realms/acme"
JWKS = f"{ISSUER}/protocol/openid-connect/certs"
DISCOVERY = f"{ISSUER}/.well-known/openid-configuration"
NOW = time.time()
RS = PrivateKey.generate("RS256", key_id="rs-1")
ES = PrivateKey.generate("ES256", key_id="es-1")
ED = PrivateKey.generate("EdDSA", key_id="ed-1")


def mint(key: PrivateKey, header: dict[str, Any] | None = None, **claims: Any) -> str:
    payload = {"iss": ISSUER, "aud": "billing-api", "sub": "4c1d", "exp": NOW + 300, **claims}
    head = {"alg": key.algorithm, "kid": key.key_id, **(header or {})}
    return jws.encode(head, payload, key.sign)


def bearer(token: str) -> Credentials:
    return Credentials(transport="http", headers={"authorization": f"Bearer {token}"})


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def provider(http: ScriptedHttp, clock: Clock | None = None, **jwks: Any) -> JwtProvider:
    keys = Jwks(JWKS, http=http, clock=clock or Clock(), **jwks)
    return JwtProvider(Issuer(ISSUER, audience="billing-api", keys=keys))


@pytest.mark.parametrize("key", [RS, ES, ED], ids=["RS256", "ES256", "EdDSA"])
def test_each_asymmetric_algorithm_verifies_against_the_published_key(
    key: PrivateKey,
) -> None:
    http = ScriptedHttp({JWKS: {"keys": [RS.jwk(), ES.jwk(), ED.jwk()]}})
    identity = provider(http).authenticate(bearer(mint(key)))
    assert identity is not None and identity.subject == "user:4c1d"


def test_a_signature_by_another_key_of_the_same_id_is_refused() -> None:
    impostor = PrivateKey.generate("ES256", key_id="es-1")
    http = ScriptedHttp({JWKS: {"keys": [ES.jwk()]}})
    with pytest.raises(exceptions.Unauthenticated, match="signature does not match"):
        provider(http).authenticate(bearer(mint(impostor)))


def test_an_hmac_token_signed_with_the_public_key_is_refused() -> None:
    public_pem = RS.public().jwk()["n"]
    forged = jws.encode(
        {"alg": "HS256", "kid": "rs-1"},
        {"iss": ISSUER, "aud": "billing-api", "sub": "4c1d", "exp": NOW + 300},
        jws.HmacKey.of("rs-1", public_pem).sign,
    )
    http = ScriptedHttp({JWKS: {"keys": [RS.jwk()]}})
    with pytest.raises(exceptions.Unauthenticated, match="HS256, not accepted"):
        provider(http).authenticate(bearer(forged))
    assert http.count(JWKS) == 0


def test_a_key_rotated_in_is_fetched_once_and_a_forged_id_waits_its_cooldown() -> None:
    clock = Clock()
    http = ScriptedHttp({JWKS: {"keys": [RS.jwk()]}})
    jwt = provider(http, clock)
    assert jwt.authenticate(bearer(mint(RS))) is not None
    http.route(JWKS, {"keys": [RS.jwk(), ES.jwk()]})
    assert jwt.authenticate(bearer(mint(ES))) is not None
    assert http.count(JWKS) == 2
    forged = PrivateKey.generate("ES256", key_id="forged")
    for _ in range(5):
        with pytest.raises(exceptions.Unauthenticated, match="does not publish"):
            jwt.authenticate(bearer(mint(forged)))
    assert http.count(JWKS) == 2
    clock.now += 31
    with pytest.raises(exceptions.Unauthenticated):
        jwt.authenticate(bearer(mint(forged)))
    assert http.count(JWKS) == 3


def test_with_nothing_cached_an_issuer_down_is_unavailable_never_a_refusal() -> None:
    http = ScriptedHttp({JWKS: EndpointUnavailable(JWKS, "could not be reached")})
    with pytest.raises(EndpointUnavailable):
        provider(http).authenticate(bearer(mint(RS)))


def test_a_published_private_key_and_a_key_for_encryption_are_left_out() -> None:
    leaked = {**ES.jwk(), "d": "private", "kid": "es-1"}
    encrypting = {**RS.jwk(), "use": "enc"}
    http = ScriptedHttp({JWKS: {"keys": [leaked, encrypting]}})
    with pytest.raises(exceptions.Unauthenticated, match="does not publish"):
        provider(http).authenticate(bearer(mint(ES)))
    with pytest.raises(exceptions.Unauthenticated, match="does not publish"):
        provider(http).authenticate(bearer(mint(RS)))


def test_the_issuer_is_discovered_on_the_first_token_never_at_construction() -> None:
    http = ScriptedHttp(
        {DISCOVERY: {"issuer": ISSUER, "jwks_uri": JWKS}, JWKS: {"keys": [ES.jwk()]}}
    )
    jwt = JwtProvider.from_issuer(
        ISSUER, audience="billing-api", mapping=ClaimMapping.keycloak(), http=http
    )
    assert http.sent == []
    assert jwt.authenticate(bearer(mint(ES))) is not None
    assert [one.target for one in http.sent] == [DISCOVERY, JWKS]
    proofs = DpopProofs("https://billing.sincpro.com.bo")
    bound = JwtProvider.from_issuer(ISSUER, audience="billing-api", http=http, dpop=proofs)
    assert bound.dpop is proofs


def test_keys_given_by_hand_verify_what_they_sign() -> None:
    by_pem = PublicKeys([RS.pem()], algorithm="RS256")
    by_jwk = PublicKeys([ES.jwk()])
    jwt = JwtProvider(
        [
            Issuer("partner-a", audience="billing-api", keys=by_pem),
            Issuer("partner-b", audience="billing-api", keys=by_jwk),
        ]
    )
    assert (
        jwt.authenticate(bearer(mint(RS, header={"kid": None}, iss="partner-a"))) is not None
    )
    assert jwt.authenticate(bearer(mint(ES, iss="partner-b"))) is not None
    assert PublicKey.from_pem(RS.pem(), "RS256").jwk()["kty"] == "RSA"


def test_a_key_that_cannot_verify_the_algorithm_is_a_programming_error() -> None:
    with pytest.raises(exceptions.ProgrammingError):
        PublicKey.from_jwk(ES.jwk(), "RS256")
    with pytest.raises(exceptions.ProgrammingError):
        Jwks(JWKS, algorithms=["HS256"])
    with pytest.raises(exceptions.ProgrammingError, match="private key"):
        PublicKey.from_jwk({**ES.jwk(), "d": "x"}, "ES256")
