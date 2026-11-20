"""`SigningKeys`: the keys this service signs its own tokens with — the newest signs, every one
verifies, so a key rotates without a window where tokens fail.

    SigningKeys.hmac({"2026-09": settings.token_secret})            HS256, the standard library
    SigningKeys.private({"2026-09": settings.private_pem}, "ES256")  [jwt]: others verify with
                                                                      the published JWKS

Context: with HMAC every verifier holds the secret — fine for one service; with a private key,
other services verify with `jwks()` and no secret is shared.
"""

from collections.abc import Callable, Mapping
from typing import Any

from sincpro_framework.auth.adapters.jwt.keys import SharedSecrets
from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.domain.keys import KeySource
from sincpro_framework.auth.infrastructure import jws


class SigningKeys:
    """Keys by id — the last given signs."""

    def __init__(
        self,
        algorithm: str,
        signers: Mapping[str, Callable[[bytes], bytes]],
        verification: KeySource,
        public: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        if not signers:
            raise exceptions.ProgrammingError("SigningKeys needs at least one key")
        self.algorithm = algorithm
        self._signers = dict(signers)
        self.key_id = list(self._signers)[-1]
        self._verification = verification
        self._public = public

    @classmethod
    def hmac(cls, keys: Mapping[str, Any], algorithm: str = "HS256") -> "SigningKeys":
        """HMAC keys by id, text or a pydantic `Secret` — the standard library."""
        secrets = SharedSecrets(keys, algorithm)
        return cls(algorithm, {kid: key.sign for kid, key in secrets.keys.items()}, secrets)

    @classmethod
    def private(cls, keys: Mapping[str, Any], algorithm: str = "ES256") -> "SigningKeys":
        """Private keys by id — `PrivateKey`s or PEM text — the `[jwt]` extra; `jwks()` publishes
        their public halves."""
        from sincpro_framework.auth.adapters.jwt.jwks import PublicKeys
        from sincpro_framework.auth.infrastructure.jose_keys import PrivateKey

        loaded = {
            kid: (
                one
                if isinstance(one, PrivateKey)
                else PrivateKey.from_pem(
                    one if isinstance(one, (str, bytes)) else one.get_secret_value(),
                    algorithm,
                    kid,
                )
            )
            for kid, one in keys.items()
        }
        for kid, one in loaded.items():
            one.key_id = kid
        public = PublicKeys([one.public() for one in loaded.values()])
        return cls(
            algorithm,
            {kid: one.sign for kid, one in loaded.items()},
            public,
            lambda: {"keys": [one.jwk() for one in loaded.values()]},
        )

    def sign(self, claims: Mapping[str, Any]) -> str:
        """`claims` signed with the newest key, its id in the header."""
        header = {"alg": self.algorithm, "typ": "at+jwt", "kid": self.key_id}
        return jws.encode(header, claims, self._signers[self.key_id])

    def verification(self) -> KeySource:
        """What verifies the tokens these keys sign."""
        return self._verification

    def jwks(self) -> dict[str, Any]:
        """The public keys to publish — `ProgrammingError` for HMAC keys, which are secrets."""
        if self._public is None:
            raise exceptions.ProgrammingError(
                "HMAC keys are secrets: there is no JWKS to publish"
            )
        return self._public()
