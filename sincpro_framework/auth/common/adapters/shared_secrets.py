"""`SharedSecrets`: the HMAC keys of an issuer this service shares a secret with — its own token
issuer, a peer — by key id, the standard library's.

    SharedSecrets({"2026-09": Secret("…")})                 HS256
    SharedSecrets({"a": old, "b": new}, algorithm="HS512")  both verify; rotate by adding

Context: a token naming no `kid` is verified only when there is exactly one key — with two, it
could be either, and guessing is how a rotated-out key goes on working. A secret shorter than the
algorithm's hash (32 bytes for HS256, RFC 7518 §3.2) is accepted and warned about once.
"""

from collections.abc import Mapping
from typing import Any

from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.domain.keys import JwsAlgorithm, KeySource, VerificationKey
from sincpro_framework.auth.common.infrastructure import jws
from sincpro_framework.sincpro_logger import logger

MINIMUM_SECRET: dict[str, int] = {
    JwsAlgorithm.HS256: 32,
    JwsAlgorithm.HS384: 48,
    JwsAlgorithm.HS512: 64,
}
"""The shortest secret each HMAC algorithm is warned under — its hash's length (RFC 7518 §3.2)."""


class SharedSecrets(KeySource):
    """HMAC keys by id, all of one algorithm."""

    symmetric = True

    def __init__(self, keys: Mapping[str, Any], algorithm: str = JwsAlgorithm.HS256) -> None:
        """`keys` by id, each text or a pydantic `Secret`; at least one, or `ProgrammingError`."""
        if not keys:
            raise exceptions.ProgrammingError("SharedSecrets needs at least one key")
        self.keys = {
            key_id: jws.HmacKey.from_secret(key_id, secret, algorithm)
            for key_id, secret in keys.items()
        }
        self.algorithms = frozenset({algorithm})
        short = [
            key_id
            for key_id, key in self.keys.items()
            if len(key.secret) < MINIMUM_SECRET[algorithm]
        ]
        if short:
            logger.warning(
                f"shared secrets shorter than {MINIMUM_SECRET[algorithm]} bytes for {algorithm}",
                keys=short,
            )

    def key_for(self, key_id: str | None, algorithm: str) -> VerificationKey:
        """The key named `key_id` — or the only key when none is named — for `algorithm`."""
        if algorithm not in self.algorithms:
            raise exceptions.Unauthenticated(f"token signed with {algorithm}, not accepted")
        if key_id is None:
            if len(self.keys) != 1:
                raise exceptions.Unauthenticated("token names no key, and there are several")
            return next(iter(self.keys.values()))
        if key_id not in self.keys:
            raise exceptions.Unauthenticated("token signed with an unknown key")
        return self.keys[key_id]
