"""The keys a signed token is verified with, and where they come from — a shared secret, a set of
public keys an issuer publishes (JWKS), keys given by hand.

    source.key_for("2026-09", "RS256").verify(signing_input, signature)   ->  True

Context: the header of a token names a key (`kid`) and an algorithm (`alg`); neither is trusted.
A `KeySource` says which algorithms it verifies — never one family for the other: an HMAC secret
never verifies an asymmetric algorithm and a public key never an HMAC one, which is the whole of
the algorithm-confusion attack — and answers the key only when the algorithm is the key's own.
"""

from abc import ABC, abstractmethod


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
