"""The keys a signed token is verified with, and where they come from — a shared secret, a set of
public keys an issuer publishes (JWKS), keys given by hand.

    source.key_for("2026-09", "RS256").verify(signing_input, signature)   ->  True

Context: the header of a token names a key (`kid`) and an algorithm (`alg`); neither is trusted.
A `KeySource` says which algorithms it verifies — never one family for the other: an HMAC secret
never verifies an asymmetric algorithm and a public key never an HMAC one, which is the whole of
the algorithm-confusion attack — and answers the key only when the algorithm is the key's own.
"""

from abc import ABC, abstractmethod
from enum import StrEnum


class JwsAlgorithm(StrEnum):
    """The JWS algorithms auth signs and verifies (RFC 7518) — HMAC with the standard library,
    the asymmetric ones with the `[jwt]` extra."""

    HS256 = "HS256"
    HS384 = "HS384"
    HS512 = "HS512"
    RS256 = "RS256"
    RS384 = "RS384"
    RS512 = "RS512"
    PS256 = "PS256"
    PS384 = "PS384"
    PS512 = "PS512"
    ES256 = "ES256"
    ES384 = "ES384"
    ES512 = "ES512"
    EDDSA = "EdDSA"


HMAC_ALGORITHMS = frozenset({JwsAlgorithm.HS256, JwsAlgorithm.HS384, JwsAlgorithm.HS512})
"""The algorithms a shared secret verifies — never shared with public keys."""

RSA_ALGORITHMS = frozenset(
    {
        JwsAlgorithm.RS256,
        JwsAlgorithm.RS384,
        JwsAlgorithm.RS512,
        JwsAlgorithm.PS256,
        JwsAlgorithm.PS384,
        JwsAlgorithm.PS512,
    }
)
"""The algorithms an RSA key verifies — PKCS#1 v1.5 and PSS."""

EC_CURVES: dict[str, str] = {
    JwsAlgorithm.ES256: "P-256",
    JwsAlgorithm.ES384: "P-384",
    JwsAlgorithm.ES512: "P-521",
}
"""The curve each ECDSA algorithm is on — an EC key verifies only its own curve's."""

ASYMMETRIC_ALGORITHMS = RSA_ALGORITHMS | frozenset(EC_CURVES) | {JwsAlgorithm.EDDSA}
"""The algorithms a public key verifies — never shared with HMAC secrets."""


class VerificationKey(ABC):
    """One key: its id, the one algorithm it verifies, and the check."""

    key_id: str | None
    algorithm: str

    @abstractmethod
    def verify(self, signing_input: bytes, signature: bytes) -> bool:
        """Whether `signature` is this key's over `signing_input` — compared in constant time."""


class KeySource(ABC):
    """Where an issuer's verification keys come from."""

    algorithms: frozenset[str]
    """The algorithms its keys verify — a token naming another is refused before any key."""

    symmetric: bool
    """Whether its keys are shared secrets (HMAC) — never mixed with public keys."""

    @abstractmethod
    def key_for(self, key_id: str | None, algorithm: str) -> VerificationKey:
        """The key a token naming `key_id` and `algorithm` is verified with.

        Context: `Unauthenticated` for a key it does not know or an algorithm that is not the
        key's; `EndpointUnavailable` when the keys could not be fetched — the caller's token
        did not fail, the service could not check it.
        """
