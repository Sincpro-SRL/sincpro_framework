"""Asymmetric JOSE keys — RSA, RSA-PSS, ECDSA and EdDSA — on PyJWT and `cryptography`: the
`[jwt]` extra. They sign and verify a JWS; the claims are `jws`'s, never PyJWT's.

    PublicKey.from_jwk({"kty": "EC", "crv": "P-256", "x": "…", "y": "…", "kid": "k1"}, "ES256")
    PublicKey.from_pem(pem, "RS256", key_id="2026-09")
    PrivateKey.generate("ES256", key_id="k1").jwk()   ->  the public JWK, to publish

Context: a key verifies exactly one algorithm, of its own family — an RSA key never an `ES*`, an
EC key only the curve it is on — so a token that names another is refused before its signature
is computed. A published JWK carrying a private part (`d`) is refused: it is a leak to report,
not a key to use.
"""

import json
from collections.abc import Mapping
from typing import Any

from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.domain.keys import VerificationKey

JWT_MISSING = (
    "asymmetric JOSE keys need PyJWT and cryptography. "
    "Install with: pip install sincpro-framework[jwt]"
)

try:  # pragma: no cover - tests/core/test_core_without_extras.py
    import jwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
except ImportError as error:  # pragma: no cover
    raise ImportError(JWT_MISSING) from error

RSA_ALGORITHMS = frozenset({"RS256", "RS384", "RS512", "PS256", "PS384", "PS512"})
EC_CURVES = {"ES256": "P-256", "ES384": "P-384", "ES512": "P-521"}
ASYMMETRIC = RSA_ALGORITHMS | frozenset(EC_CURVES) | frozenset({"EdDSA"})


def fits(jwk: Mapping[str, Any], algorithm: str) -> bool:
    """Whether the JWK's type — and curve — can verify `algorithm`, and its own `alg`, when it
    names one, is that algorithm.

        fits({"kty": "EC", "crv": "P-256"}, "ES256")   ->  True
        fits({"kty": "EC", "crv": "P-256"}, "ES384")   ->  False
        fits({"kty": "RSA", "alg": "RS256"}, "PS256")  ->  False
    """
    named = jwk.get("alg")
    if named is not None and named != algorithm:
        return False
    kty = jwk.get("kty")
    if algorithm in RSA_ALGORITHMS:
        return kty == "RSA"
    if algorithm in EC_CURVES:
        return kty == "EC" and jwk.get("crv") == EC_CURVES[algorithm]
    return algorithm == "EdDSA" and kty == "OKP" and jwk.get("crv") in ("Ed25519", "Ed448")


def _algorithm(algorithm: str) -> Any:
    """PyJWT's implementation of `algorithm`, asymmetric ones only."""
    if algorithm not in ASYMMETRIC:
        raise exceptions.ProgrammingError(f"{algorithm} is not an asymmetric JOSE algorithm")
    return jwt.get_algorithm_by_name(algorithm)


class PublicKey(VerificationKey):
    """A public key that verifies one asymmetric algorithm."""

    def __init__(self, key_id: str | None, algorithm: str, key: Any) -> None:
        """`key` is a `cryptography` public key of the algorithm's family."""
        self.key_id = key_id
        self.algorithm = algorithm
        self._implementation = _algorithm(algorithm)
        self._key = key

    @classmethod
    def from_jwk(cls, jwk: Mapping[str, Any], algorithm: str) -> "PublicKey":
        """The key of a public JWK, for `algorithm` — `ProgrammingError` when the JWK holds a
        private part or does not fit the algorithm."""
        if "d" in jwk:
            raise exceptions.ProgrammingError(
                "a published JWK holds a private key — refused; rotate it, it leaked"
            )
        if not fits(jwk, algorithm):
            raise exceptions.ProgrammingError(
                f"the JWK {jwk.get('kid')!r} does not verify {algorithm}"
            )
        key = _algorithm(algorithm).from_jwk(json.dumps(dict(jwk)))
        return cls(jwk.get("kid"), algorithm, key)

    @classmethod
    def from_pem(
        cls, pem: str | bytes, algorithm: str, key_id: str | None = None
    ) -> "PublicKey":
        """The key of a PEM — a public key, or the public half of a private one."""
        raw = pem.encode() if isinstance(pem, str) else pem
        try:
            loaded: Any = serialization.load_pem_public_key(raw)
        except ValueError:
            loaded = serialization.load_pem_private_key(raw, password=None).public_key()
        return cls(key_id, algorithm, loaded)

    def verify(self, signing_input: bytes, signature: bytes) -> bool:
        """Whether `signature` is this key's over `signing_input` — any failure is `False`."""
        try:
            return bool(self._implementation.verify(signing_input, self._key, signature))
        except Exception:
            return False

    def jwk(self) -> dict[str, Any]:
        """The public JWK, with its `kid`, `alg` and `use`."""
        found = self._implementation.to_jwk(self._key, as_dict=True)
        found.pop("key_ops", None)
        return {
            **found,
            **({"kid": self.key_id} if self.key_id else {}),
            "alg": self.algorithm,
            "use": "sig",
        }


class PrivateKey:
    """A private key that signs one asymmetric algorithm — this service's own issuer, a client
    assertion, a DPoP proof."""

    def __init__(self, key_id: str | None, algorithm: str, key: Any) -> None:
        self.key_id = key_id
        self.algorithm = algorithm
        self._implementation = _algorithm(algorithm)
        self._key = key

    @classmethod
    def from_pem(
        cls,
        pem: str | bytes,
        algorithm: str,
        key_id: str | None = None,
        password: bytes | None = None,
    ) -> "PrivateKey":
        """The key of a private PEM — text or a pydantic `Secret`'s value."""
        raw = pem.encode() if isinstance(pem, str) else pem
        return cls(
            key_id, algorithm, serialization.load_pem_private_key(raw, password=password)
        )

    @classmethod
    def generate(cls, algorithm: str = "ES256", key_id: str | None = None) -> "PrivateKey":
        """A new key for `algorithm` — RSA 2048 for `RS*`/`PS*`, the curve for `ES*`, Ed25519."""
        if algorithm in RSA_ALGORITHMS:
            key: Any = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        elif algorithm in EC_CURVES:
            curve = {
                "ES256": ec.SECP256R1(),
                "ES384": ec.SECP384R1(),
                "ES512": ec.SECP521R1(),
            }
            key = ec.generate_private_key(curve[algorithm])
        elif algorithm == "EdDSA":
            key = ed25519.Ed25519PrivateKey.generate()
        else:
            raise exceptions.ProgrammingError(
                f"{algorithm} is not an asymmetric JOSE algorithm"
            )
        return cls(key_id, algorithm, key)

    def sign(self, signing_input: bytes) -> bytes:
        """The signature of `signing_input`, as JWS writes it (ECDSA as r‖s)."""
        return self._implementation.sign(signing_input, self._key)

    def public(self) -> PublicKey:
        """The public half."""
        return PublicKey(self.key_id, self.algorithm, self._key.public_key())

    def jwk(self) -> dict[str, Any]:
        """The public JWK to publish — never the private part."""
        return self.public().jwk()

    def pem(self) -> bytes:
        """The private key as an unencrypted PKCS#8 PEM — to store as a secret."""
        return self._key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )

    def __repr__(self) -> str:
        return f"PrivateKey(key_id={self.key_id!r}, algorithm={self.algorithm!r})"
