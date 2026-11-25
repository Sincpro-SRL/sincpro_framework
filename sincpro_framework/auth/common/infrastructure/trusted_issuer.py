"""`TrustedIssuer`: one issuer a verifier trusts — who it is, which audience its tokens name,
the keys that verify them, how their claims read and how much clock skew is tolerated. What an
issuer and its verifiers agree on, so the issuer publishes it (`TokenIssuer.trusted_issuer()`)
and a verifier (`JwtProvider`) checks by it."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import timedelta

from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.domain.keys import HMAC_ALGORITHMS, KeySource
from sincpro_framework.auth.common.infrastructure import jws
from sincpro_framework.auth.common.infrastructure.claim_mapping import ClaimMapping


@dataclass(frozen=True)
class TrustedIssuer:
    """One issuer this service trusts: who it is, which audience its tokens must name, where its
    keys are, how its claims read, and how much clock skew is tolerated."""

    issuer: str
    audience: str | Iterable[str]
    keys: KeySource
    mapping: ClaimMapping = field(default_factory=ClaimMapping)
    leeway: timedelta = timedelta(seconds=60)
    required: frozenset[str] = frozenset({"exp", "sub"})
    authorized_parties: frozenset[str] | None = None
    """The clients (`azp`) whose tokens are accepted — every client when `None`."""
    algorithms: frozenset[str] | None = None
    """The algorithms accepted — the key source's when `None`; a subset of them otherwise."""

    def __post_init__(self) -> None:
        """Checked once, at construction — a `ProgrammingError` each:

        1. No audience: every token of the issuer would be accepted, whichever service it was
           issued for — the audience check Odoo's `auth_oauth` left as a comment.
        2. `none`, or an algorithm of the other family than the keys' — HMAC for public keys,
           public keys for a secret: the algorithm-confusion attack.
        Final: the audience held as a set, the algorithms resolved.
        """
        audiences = (
            frozenset({self.audience})
            if isinstance(self.audience, str)
            else frozenset(self.audience)
        )
        if not audiences:
            raise exceptions.ProgrammingError(
                f"TrustedIssuer {self.issuer!r} names no audience"
            )
        algorithms = self.algorithms if self.algorithms is not None else self.keys.algorithms
        if not algorithms or "none" in algorithms or not algorithms <= self.keys.algorithms:
            raise exceptions.ProgrammingError(
                f"TrustedIssuer {self.issuer!r}: algorithms {sorted(algorithms)} are not all ones "
                f"its keys verify ({sorted(self.keys.algorithms)})"
            )
        if self.keys.symmetric != (algorithms <= HMAC_ALGORITHMS):
            raise exceptions.ProgrammingError(
                f"TrustedIssuer {self.issuer!r}: HMAC and public-key algorithms never share keys"
            )
        object.__setattr__(self, "audience", audiences)
        object.__setattr__(self, "algorithms", algorithms)

    @property
    def audiences(self) -> frozenset[str]:
        """The audiences, as a set."""
        return frozenset(self.audience)

    def rules(self) -> jws.ClaimRules:
        """The registered-claim rules its tokens are checked by."""
        return jws.ClaimRules(
            issuer=self.issuer,
            audiences=self.audiences,
            leeway=self.leeway.total_seconds(),
            required=self.required,
        )
