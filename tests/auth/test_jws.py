"""The JWS core every JWT reader shares: RFC 7515's own example verifies, a header never picks
its algorithm, and a JWT's registered claims are refused at the edge of their leeway."""

import base64
import json

import pytest

from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.infrastructure import jws

RFC7515_A1 = (
    "eyJ0eXAiOiJKV1QiLA0KICJhbGciOiJIUzI1NiJ9"
    ".eyJpc3MiOiJqb2UiLA0KICJleHAiOjEzMDA4MTkzODAsDQogImh0dHA6Ly9leGFtcGxlLmNvbS9pc19yb290Ijp0cnVlfQ"
    ".dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
)
RFC7515_A1_KEY = jws.decoded(
    "AyM1SysPpbyDfgZld3umj1qzKObwVMkoqQ-EstJQLr_T-1qS0gZH75aKtMN3Yj0iPS4hcgUuTwjAzZr1Z9CAow"
)


def test_the_example_of_rfc_7515_verifies_and_a_changed_signature_does_not() -> None:
    parsed = jws.parse(RFC7515_A1)
    key = jws.HmacKey(None, RFC7515_A1_KEY)
    assert parsed.claims["iss"] == "joe" and parsed.algorithm == "HS256"
    assert key.verifies(parsed)
    tampered = jws.parse(RFC7515_A1[:-2] + ("Xk" if RFC7515_A1[-2:] != "Xk" else "Yk"))
    assert not key.verifies(tampered)


def test_a_token_made_here_reads_back_the_same() -> None:
    key = jws.HmacKey.of("k1", "a secret of thirty-two characters!")
    token = jws.encode({"alg": "HS256", "kid": "k1"}, {"sub": "user:42", "n": 1}, key.sign)
    parsed = jws.parse(token)
    assert (parsed.key_id, dict(parsed.claims)) == ("k1", {"sub": "user:42", "n": 1})
    assert key.verifies(parsed)
    assert jws.looks_like_jws(token)


def test_a_header_naming_another_algorithm_is_never_verified_by_the_key() -> None:
    key = jws.HmacKey.of("k1", "s")
    token = jws.encode(
        {"alg": "HS512", "kid": "k1"}, {"sub": "u"}, jws.HmacKey.of("k1", "s", "HS512").sign
    )
    assert not key.verifies(jws.parse(token))
    unsigned = jws.encode({"alg": "none"}, {"sub": "u"}, lambda _: b"")
    assert not key.verifies(jws.parse(unsigned))


@pytest.mark.parametrize(
    "token",
    [
        "not-a-token",
        "a.b",
        "a.b.c.d",
        f"{jws.encoded(b'[1]')}.{jws.encoded(b'{}')}.c2ln",
        f"{jws.encoded(b'{}')}.{jws.encoded(b'not json')}.c2ln",
        "x" * (jws.MAX_TOKEN_LENGTH + 1),
        "eyJ+YWxn.eyJz.c2ln",
        f"{jws.encoded(b'[' * 12_000)}.{jws.encoded(b'{}')}.c2ln",
    ],
    ids=[
        "no-dots",
        "two-parts",
        "four-parts",
        "header-not-an-object",
        "payload-not-json",
        "too-long",
        "not-base64url",
        "nested-deeper-than-the-parser-recurses",
    ],
)
def test_what_is_not_a_compact_jws_is_malformed(token: str) -> None:
    with pytest.raises(exceptions.Unauthenticated, match="malformed token"):
        jws.parse(token)


BASE64URL = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def test_a_signature_spelled_another_way_is_malformed() -> None:
    """The incident: a 32-byte signature is 43 characters, and the last one carries 2 bits no
    byte reads — a token changed there decoded to the same signature and still verified, about
    once in a thousand tokens."""
    key = jws.HmacKey.of("k1", "secret")
    token = jws.encode({"alg": "HS256", "kid": "k1"}, {"sub": "u"}, key.sign)
    head, claims, signature = token.split(".")
    respelled = signature[:-1] + BASE64URL[BASE64URL.index(signature[-1]) ^ 0b01]
    assert base64.urlsafe_b64decode(respelled + "=") == base64.urlsafe_b64decode(
        signature + "="
    )
    with pytest.raises(exceptions.Unauthenticated, match="malformed token"):
        jws.parse(f"{head}.{claims}.{respelled}")
    assert key.verifies(jws.parse(token))


def test_an_opaque_token_does_not_look_like_a_jws() -> None:
    assert not jws.looks_like_jws("rt_Q2x9abc")
    assert not jws.looks_like_jws("a.b")
    assert not jws.looks_like_jws("a+b.c.d")


def test_an_algorithm_that_is_not_hmac_is_a_programming_error() -> None:
    with pytest.raises(exceptions.ProgrammingError):
        jws.HmacKey.of("k", "s", "RS256")


NOW = 1_900_000_000.0


@pytest.mark.parametrize(
    ("claims", "reason"),
    [
        ({"exp": NOW - 61}, "expired"),
        ({"exp": NOW - 60}, "expired"),
        ({"exp": NOW, "nbf": NOW + 61}, "not valid yet"),
        ({"exp": NOW + 10, "iat": NOW + 61}, "issued in the future"),
        ({"exp": True}, "is not a date"),
        ({"exp": "soon"}, "is not a date"),
        ({}, "lacks exp"),
        ({"exp": NOW + 10, "aud": "other"}, "another audience"),
        ({"exp": NOW + 10, "aud": ["other", "more"]}, "another audience"),
        ({"exp": NOW + 10, "aud": "billing", "iss": "https://elsewhere"}, "another issuer"),
    ],
)
def test_claims_outside_the_rules_are_refused_saying_why(claims: dict, reason: str) -> None:
    rules = jws.ClaimRules(issuer="https://issuer", audiences=frozenset({"billing"}))
    claims = {"iss": "https://issuer", "aud": "billing", **claims}
    with pytest.raises(exceptions.Unauthenticated, match=reason):
        rules.check(claims, now=NOW)


def test_claims_inside_the_leeway_are_accepted() -> None:
    rules = jws.ClaimRules(issuer="https://issuer", audiences=frozenset({"billing"}))
    rules.check(
        {
            "iss": "https://issuer",
            "aud": ["mobile", "billing"],
            "exp": NOW - 59,
            "nbf": NOW + 59,
            "iat": NOW + 59,
        },
        now=NOW,
    )


def test_an_empty_set_of_audiences_is_never_a_configuration() -> None:
    with pytest.raises(exceptions.ProgrammingError):
        jws.ClaimRules(audiences=frozenset())


def test_a_claim_of_names_is_read_from_a_string_or_a_list() -> None:
    assert jws.text_set("openid billing:read") == {"openid", "billing:read"}
    assert jws.text_set(["a", 1, "b"]) == {"a", "b"}
    assert jws.text_set({"a": 1}) == frozenset() and jws.text_set(None) == frozenset()


def test_the_payload_is_compact_json() -> None:
    token = jws.encode(
        {"alg": "HS256"}, {"a": 1, "b": [1, 2]}, jws.HmacKey.of(None, "s").sign
    )
    assert (
        jws.decoded(token.split(".")[1])
        == json.dumps({"a": 1, "b": [1, 2]}, separators=(",", ":")).encode()
    )
