"""`PublicKeys`: public keys given by hand — a partner's PEM, a JWK copied from its
documentation, the public halves of this service's own signing keys: what a signer publishes
and a verifier checks with. The `[jwt]` extra."""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from sincpro_framework.auth.common.adapters.jose_keys import ASYMMETRIC, PublicKey
from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.domain.keys import KeySource, VerificationKey


def asymmetric_algorithms(algorithms: Iterable[str]) -> frozenset[str]:
    """`algorithms` as a set — `ProgrammingError` for one that is not asymmetric."""
    chosen = frozenset(algorithms)
    if not chosen or not chosen <= ASYMMETRIC:
        raise exceptions.ProgrammingError(
            f"public keys verify asymmetric algorithms only: {sorted(chosen - ASYMMETRIC)}"
        )
    return chosen


class PublicKeys(KeySource):
    """Public keys given by hand — a partner's PEM, a JWK copied from its documentation."""

    symmetric = False

    def __init__(
        self,
        keys: Sequence[PublicKey | Mapping[str, Any] | str | bytes],
        algorithm: str | None = None,
    ) -> None:
        """A JWK is read for its own `alg` (or `algorithm`); a PEM needs `algorithm`."""
        built: list[PublicKey] = []
        for one in keys:
            if isinstance(one, PublicKey):
                built.append(one)
            elif isinstance(one, Mapping):
                named = one.get("alg") or algorithm
                if named is None:
                    raise exceptions.ProgrammingError("a JWK naming no alg needs algorithm=")
                built.append(PublicKey.from_jwk(one, named))
            else:
                if algorithm is None:
                    raise exceptions.ProgrammingError("a PEM key needs algorithm=")
                built.append(PublicKey.from_pem(one, algorithm))
        if not built:
            raise exceptions.ProgrammingError("PublicKeys needs at least one key")
        self.keys = built
        self.algorithms = asymmetric_algorithms(one.algorithm for one in built)

    def key_for(self, key_id: str | None, algorithm: str) -> VerificationKey:
        """The key named `key_id` for `algorithm` — or the only one for it when none is named."""
        fitting = [one for one in self.keys if one.algorithm == algorithm]
        if key_id is not None:
            found = next((one for one in fitting if one.key_id == key_id), None)
        else:
            found = fitting[0] if len(fitting) == 1 else None
        if found is None:
            raise exceptions.Unauthenticated("token signed with an unknown key")
        return found
