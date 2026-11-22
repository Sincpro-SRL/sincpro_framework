"""A JWS in compact form over the standard library: parsed, signed and verified with HMAC, and
the registered claims of a JWT checked.

    token = encode({"alg": "HS256", "kid": "k1"}, {"sub": "user:42", "exp": 1_900_000_000},
                   HmacKey.of("k1", "secret").sign)
    jws = parse(token)                         ->  Jws(header={...}, claims={...}, ...)
    HmacKey.of("k1", "secret").verifies(jws)   ->  True
    ClaimRules(issuer="https://…", audiences={"billing"}).check(jws.claims, now=time.time())

Context: every reader of a JWT in the framework — the service token, `JwtProvider`, the token
issuer, a DPoP proof — parses and checks the claims here, one way, against a clock it is handed.
The asymmetric signatures are PyJWT's (`jose_keys`, the `[jwt]` extra); the claims never are. A
header never chooses how the token is verified: whoever verifies says which algorithms it takes.
"""

import base64
import binascii
import hashlib
import hmac
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.domain.keys import VerificationKey
from sincpro_framework.auth.infrastructure.digests import revealed

MAX_TOKEN_LENGTH = 16_384
"""Longer than any token an identity provider issues: a bigger one is refused before it is
decoded, so nobody makes the process parse megabytes of JSON."""

HMAC_DIGESTS: Mapping[str, Callable[..., Any]] = {
    "HS256": hashlib.sha256,
    "HS384": hashlib.sha384,
    "HS512": hashlib.sha512,
}

_SEGMENT = re.compile(r"^[A-Za-z0-9_-]+$")


def encoded(raw: bytes) -> str:
    """`raw` as base64url without padding — the JOSE alphabet (RFC 7515 §2)."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def decoded(text: str) -> bytes:
    """The bytes of an unpadded base64url `text`; `ValueError` for anything else.

    Context: strict — a `+`, a `/` or padding is not base64url, so two spellings of one
    signature never both verify.
    """
    if text and not _SEGMENT.match(text):
        raise ValueError("not base64url")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except binascii.Error as error:
        raise ValueError("not base64url") from error


def looks_like_jws(token: str) -> bool:
    """Whether `token` has the shape of a compact JWS — three base64url segments — without
    decoding it: what tells a JWT apart from an opaque token, which another provider reads.

        looks_like_jws("eyJh.eyJz.c2ln")   ->  True
        looks_like_jws("rt_Q2x9")          ->  False
    """
    parts = token.split(".")
    return (
        len(token) <= MAX_TOKEN_LENGTH
        and len(parts) == 3
        and all(_SEGMENT.match(part) for part in parts[:2])
        and (parts[2] == "" or bool(_SEGMENT.match(parts[2])))
    )


@dataclass(frozen=True)
class Jws:
    """A compact JWS, parsed and not yet verified — nothing in it is trusted until a key says so."""

    header: Mapping[str, Any]
    claims: Mapping[str, Any]
    signing_input: bytes
    """`base64url(header) . base64url(payload)` — what the signature covers."""
    signature: bytes

    @property
    def algorithm(self) -> str:
        """The `alg` the header names — a claim to check, never an instruction to follow."""
        return str(self.header.get("alg", ""))

    @property
    def key_id(self) -> str | None:
        """The `kid` the header names, if any."""
        kid = self.header.get("kid")
        return None if kid is None else str(kid)


def _object(segment: str) -> dict[str, Any]:
    """A JSON object out of a base64url segment; `ValueError` when it is not one."""
    value = json.loads(decoded(segment))
    if not isinstance(value, dict):
        raise ValueError("not a JSON object")
    return value


def parse(token: str, what: str = "token") -> Jws:
    """`token` split and decoded — `Unauthenticated("malformed <what>")` when it is not a compact
    JWS with a JSON object in its header and its payload.

        parse("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1In0.c2ln")
            ->  Jws(header={"alg": "HS256"}, claims={"sub": "u"}, ...)
        parse("not-a-token")   ->  raises Unauthenticated("malformed token")

    Context: a header or a payload nested deeper than the parser recurses is malformed too —
    a token nobody signed must never answer the caller with an internal error.
    """
    if len(token) > MAX_TOKEN_LENGTH:
        raise exceptions.Unauthenticated(f"malformed {what}")
    try:
        header_part, payload_part, signature_part = token.split(".")
        return Jws(
            header=_object(header_part),
            claims=_object(payload_part),
            signing_input=f"{header_part}.{payload_part}".encode(),
            signature=decoded(signature_part),
        )
    except (ValueError, RecursionError) as error:
        raise exceptions.Unauthenticated(f"malformed {what}") from error


def encode(
    header: Mapping[str, Any], claims: Mapping[str, Any], sign: Callable[[bytes], bytes]
) -> str:
    """A compact JWS of `header` and `claims`, signed by `sign` over the signing input.

    Context: compact JSON (no spaces), so the same claims always give the same token.
    """
    signing_input = ".".join(
        encoded(json.dumps(part, separators=(",", ":")).encode()) for part in (header, claims)
    )
    return f"{signing_input}.{encoded(sign(signing_input.encode()))}"


@dataclass(frozen=True)
class HmacKey(VerificationKey):
    """A shared secret that signs and verifies one HMAC algorithm — HS256 by default.

    HmacKey.of("2026-09", Secret("…"), "HS256")
    """

    key_id: str | None
    secret: bytes = field(repr=False)
    algorithm: str = "HS256"

    def __post_init__(self) -> None:
        """An algorithm that is not HMAC is a `ProgrammingError` at construction."""
        if self.algorithm not in HMAC_DIGESTS:
            raise exceptions.ProgrammingError(
                f"{self.algorithm} is not an HMAC algorithm: one of {sorted(HMAC_DIGESTS)}"
            )

    @classmethod
    def of(cls, key_id: str | None, secret: Any, algorithm: str = "HS256") -> "HmacKey":
        """A key out of a secret given as text or as a pydantic `Secret`."""
        return cls(key_id, revealed(secret).encode(), algorithm)

    def sign(self, signing_input: bytes) -> bytes:
        """The HMAC of `signing_input`."""
        return hmac.new(self.secret, signing_input, HMAC_DIGESTS[self.algorithm]).digest()

    def verify(self, signing_input: bytes, signature: bytes) -> bool:
        """Whether `signature` is this key's HMAC of `signing_input` — in constant time."""
        return hmac.compare_digest(self.sign(signing_input), signature)

    def verifies(self, jws: Jws) -> bool:
        """Whether `jws` names this key's algorithm and its signature is this key's."""
        return jws.algorithm == self.algorithm and self.verify(
            jws.signing_input, jws.signature
        )


def numeric_date(claims: Mapping[str, Any], name: str, what: str = "token") -> float | None:
    """The NumericDate `claims[name]` — seconds since the epoch — or `None` when absent;
    `Unauthenticated` when it is there and not a number.

    Context: `True` is an `int` to Python and never a date to JOSE.
    """
    value = claims.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise exceptions.Unauthenticated(f"{what}'s {name} is not a date")
    return float(value)


def moment(claims: Mapping[str, Any], name: str) -> datetime | None:
    """The NumericDate `claims[name]` as a UTC `datetime`, or `None` when absent."""
    seconds = numeric_date(claims, name)
    return None if seconds is None else datetime.fromtimestamp(seconds, UTC)


def audiences_of(claims: Mapping[str, Any]) -> frozenset[str]:
    """The `aud` claim as a set — RFC 7519 allows a string or an array."""
    aud = claims.get("aud")
    if aud is None:
        return frozenset()
    if isinstance(aud, str):
        return frozenset({aud})
    if isinstance(aud, list) and all(isinstance(one, str) for one in aud):
        return frozenset(aud)
    raise exceptions.Unauthenticated("token's aud is not a string or a list of strings")


@dataclass(frozen=True)
class ClaimRules:
    """What a JWT's registered claims must say to be accepted — RFC 7519 §4.1, with `leeway`
    seconds of clock skew either way.

        ClaimRules(issuer="https://auth.sincpro.com.bo/realms/sincpro",
                   audiences=frozenset({"billing"}), required=frozenset({"exp", "sub"}))
            .check(claims, now=time.time())

    Context: `audiences=None` does not check `aud` — only for a token whose audience is checked
    another way (the service token's own `aud`); an empty set is never what anyone means.
    """

    issuer: str | None = None
    audiences: frozenset[str] | None = None
    leeway: float = 60.0
    required: frozenset[str] = frozenset({"exp"})
    what: str = "token"
    """How a refusal names the credential: `token`, `service token`, `DPoP proof`."""

    def __post_init__(self) -> None:
        """An empty set of audiences is a `ProgrammingError`: it would accept no token, and is
        always a configuration that forgot to say which."""
        if self.audiences is not None and not self.audiences:
            raise exceptions.ProgrammingError("ClaimRules(audiences=…) names no audience")

    def check(self, claims: Mapping[str, Any], now: float) -> None:
        """Refuse `claims` with `Unauthenticated` saying why, or accept them silently.

        1. Every required claim is present.
        2. `iss` is the issuer, when one is named.
        3. `aud` names one of the audiences, when they are named.
        4. `exp`: refused from `exp + leeway` on.
        5. `nbf`: refused before `nbf - leeway`.
        Final: `iat` later than `now + leeway` is refused — a token from the future is forged
        or comes from a clock that cannot be trusted.
        """
        for name in sorted(self.required):
            if claims.get(name) is None:
                raise exceptions.Unauthenticated(f"{self.what} lacks {name}")
        if self.issuer is not None and claims.get("iss") != self.issuer:
            raise exceptions.Unauthenticated(f"{self.what} is from another issuer")
        if self.audiences is not None and not audiences_of(claims) & self.audiences:
            raise exceptions.Unauthenticated(f"{self.what} is for another audience")
        expires = numeric_date(claims, "exp", self.what)
        if expires is not None and now >= expires + self.leeway:
            raise exceptions.Unauthenticated(f"{self.what} expired")
        not_before = numeric_date(claims, "nbf", self.what)
        if not_before is not None and now < not_before - self.leeway:
            raise exceptions.Unauthenticated(f"{self.what} is not valid yet")
        issued = numeric_date(claims, "iat", self.what)
        if issued is not None and issued > now + self.leeway:
            raise exceptions.Unauthenticated(f"{self.what} was issued in the future")


def text_set(value: Any) -> frozenset[str]:
    """A claim that holds names as a set: a space-separated string (`scope`) or a list of
    strings (`scp`, `roles`); anything else is no names.

        text_set("openid billing:read")       ->  {"openid", "billing:read"}
        text_set(["admin", "accountant"])     ->  {"admin", "accountant"}
    """
    if isinstance(value, str):
        return frozenset(value.split())
    if isinstance(value, Iterable) and not isinstance(value, (bytes, Mapping)):
        return frozenset(one for one in value if isinstance(one, str))
    return frozenset()


THUMBPRINT_MEMBERS = {
    "EC": ("crv", "kty", "x", "y"),
    "RSA": ("e", "kty", "n"),
    "OKP": ("crv", "kty", "x"),
    "oct": ("k", "kty"),
}


def thumbprint(jwk: Mapping[str, Any]) -> str:
    """The JWK's SHA-256 thumbprint, base64url — RFC 7638: its required members, sorted, as
    compact JSON. What a DPoP-bound token names as `cnf.jkt`.

    Context: `ProgrammingError` for a key type RFC 7638 names no members for.
    """
    members = THUMBPRINT_MEMBERS.get(str(jwk.get("kty")))
    if members is None:
        raise exceptions.ProgrammingError(f"no thumbprint for a {jwk.get('kty')!r} key")
    canonical = json.dumps(
        {name: jwk[name] for name in members}, separators=(",", ":"), sort_keys=True
    )
    return encoded(hashlib.sha256(canonical.encode()).digest())


def der_of(certificate: bytes) -> bytes:
    """A certificate as DER — given as DER, or as PEM (what an ASGI server hands)."""
    text = certificate.strip()
    if not text.startswith(b"-----BEGIN"):
        return certificate
    body = b"".join(line for line in text.splitlines() if not line.startswith(b"-----"))
    return base64.b64decode(body)


def certificate_thumbprint(certificate: bytes) -> str:
    """The certificate's SHA-256 thumbprint, base64url — RFC 8705's `x5t#S256`."""
    return encoded(hashlib.sha256(der_of(certificate)).digest())
