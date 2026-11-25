"""`JwtProvider`: a bearer JWT verified against the issuers this service trusts — Keycloak, Entra,
Google, its own token issuer — and turned into an `Identity`.

    keycloak = TrustedIssuer(
        "https://auth.sincpro.com.bo/realms/sincpro",
        audience="billing-api",
        keys=Jwks("https://auth.sincpro.com.bo/realms/sincpro/protocol/openid-connect/certs"),
        mapping=ClaimMapping.keycloak(client="billing-api"),
    )
    own = TrustedIssuer("billing", audience="billing", keys=SharedSecrets({"2026-09": secret}))
    jwt = JwtProvider([keycloak, own], resolvers=[GrantResolver(catalog, grants)],
                      revocations=KeyValueRevocations())
    JwtProvider.from_issuer(url, audience="billing-api", mapping=...)    by OIDC discovery

Context: the token's `iss` — read before it is verified, only to route — picks the issuer; an
issuer this provider does not trust is not its kind (`None`), so another provider may claim the
token. Everything else is checked against that issuer and never taken from the token: the
algorithms, the key, the audience. A known issuer's token that fails is `Unauthenticated`; keys
that cannot be fetched are `EndpointUnavailable` (503) — the caller's token did not fail.
"""

import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.domain.identity import Credentials, Identity
from sincpro_framework.auth.common.domain.keys import KeySource
from sincpro_framework.auth.common.domain.provider import AuthProvider
from sincpro_framework.auth.common.domain.resolver import IdentityResolver, resolved
from sincpro_framework.auth.common.domain.revocations import (
    Revocations,
    related_of,
    remembering_versions,
)
from sincpro_framework.auth.common.infrastructure import jws
from sincpro_framework.auth.server.adapters.bearer_tokens.claim_mapping import ClaimMapping
from sincpro_framework.auth.server.adapters.bearer_tokens.protected_resource import (
    ProtectedResource,
)

if TYPE_CHECKING:
    from sincpro_framework.auth.server.adapters.bearer_tokens.dpop_proofs import DpopProofs

HMAC_ALGORITHMS = frozenset(jws.HMAC_DIGESTS)
"""The headers read are `alg` and `kid`. `crit` names an extension that must be understood —
none is — so a token carrying it is refused; `jku`, `x5u`, `jwk` and `x5c` would let the token
say which key verifies it, and are ignored."""


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


def _quoted(text: str) -> str:
    """`text` safe inside a quoted `WWW-Authenticate` parameter."""
    return text.replace("\\", "\\\\").replace('"', "'")


class JwtProvider(AuthProvider):
    """Bearer JWTs of the issuers given, verified and mapped; the identities resolved further."""

    def __init__(
        self,
        issuers: TrustedIssuer | Sequence[TrustedIssuer],
        resolvers: Sequence[IdentityResolver] = (),
        revocations: Revocations | None = None,
        resource: ProtectedResource | None = None,
        realm: str = "sincpro",
        name: str = "jwt",
        clock: Callable[[], float] = time.time,
        dpop: "DpopProofs | None" = None,
    ) -> None:
        """`issuers` trusted, by their `iss`; `resolvers` run on every identity; `revocations`
        asked about every token; `resource` named in a 401 (RFC 9728); `clock` in seconds.
        Two issuers of one name are a `ProgrammingError`."""
        listed = [issuers] if isinstance(issuers, TrustedIssuer) else list(issuers)
        self.issuers = {one.issuer: one for one in listed}
        if len(self.issuers) != len(listed):
            raise exceptions.ProgrammingError("JwtProvider names one issuer twice")
        self.resolvers = tuple(resolvers)
        self.revocations = revocations
        self.resource = resource
        self.realm = realm
        self.name = name
        self.clock = clock
        self.dpop = dpop

    @classmethod
    def from_issuer(
        cls,
        issuer: str,
        audience: str | Iterable[str],
        mapping: ClaimMapping | None = None,
        algorithms: Iterable[str] = ("RS256", "PS256", "ES256", "EdDSA"),
        http: Any = None,
        discovery: Any = None,
        **options: Any,
    ) -> "JwtProvider":
        """A provider for one issuer whose keys are found by OIDC discovery — the `[jwt]` extra.

            JwtProvider.from_issuer("https://auth.sincpro.com.bo/realms/sincpro",
                                    audience="billing-api",
                                    mapping=ClaimMapping.keycloak(client="billing-api"))

        Context: nothing is fetched here — the discovery document and the keys are read on the
        first token, so a service starts while its identity provider is down.
        """
        from sincpro_framework.auth.server.adapters.bearer_tokens.jwks import Jwks

        keys = Jwks.discovered(issuer, algorithms=algorithms, http=http, discovery=discovery)
        provider_options = {
            name: options.pop(name)
            for name in (
                "resolvers",
                "revocations",
                "resource",
                "realm",
                "dpop",
                "name",
                "clock",
            )
            if name in options
        }
        return cls(
            TrustedIssuer(issuer, audience, keys, mapping or ClaimMapping(), **options),
            **provider_options,
        )

    def security_scheme(self) -> dict[str, Any] | None:
        """HTTP bearer, a JWT."""
        return {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}

    def challenge_for(self, refusal: exceptions.Unauthenticated | None) -> str | None:
        """`Bearer realm=…`, with `error="invalid_token"` and its reason for a token that
        failed — `insufficient_user_authentication` with `acr_values` and `max_age` for a
        step-up (RFC 9470), no error at all for a caller that presented none (RFC 6750 §3.1) —
        and the resource metadata URL when there is one (RFC 9728).

            Bearer realm="sincpro", error="invalid_token", error_description="token expired"
            Bearer realm="sincpro"                                    nothing was presented
        """
        parts = [f'Bearer realm="{_quoted(self.realm)}"']
        if refusal is not None and refusal.step_up is not None:
            parts.append('error="insufficient_user_authentication"')
            parts.append(f'error_description="{_quoted(refusal.reason)}"')
            parts.append(f'acr_values="{_quoted(refusal.step_up)}"')
            if refusal.max_age is not None:
                parts.append(f"max_age={refusal.max_age}")
        elif refusal is not None and refusal.reason and not refusal.anonymous:
            parts.append('error="invalid_token"')
            parts.append(f'error_description="{_quoted(refusal.reason)}"')
        if self.resource is not None:
            parts.append(f'resource_metadata="{self.resource.metadata_url}"')
        if self.dpop is not None:
            parts.append(self.dpop.challenge())
        return ", ".join(parts)

    def challenge(self) -> str | None:
        """The challenge of a call that presented nothing: `Bearer realm=…`, with no error."""
        return self.challenge_for(None)

    def _issuer_of(self, parsed: jws.Jws) -> TrustedIssuer | None:
        """The issuer the unverified `iss` names, if this provider trusts it."""
        named = parsed.claims.get("iss")
        return self.issuers.get(named) if isinstance(named, str) else None

    def _verified(self, parsed: jws.Jws, issuer: TrustedIssuer) -> None:
        """The token's header, signature and claims checked against `issuer`.

        1. A critical header it does not understand, or an algorithm the issuer does not use:
           refused before any key is looked up.
        2. The key the issuer's source answers for the `kid` and the algorithm; its signature.
        3. The registered claims; a `typ` the mapping refuses (an ID or refresh token).
        Final: an authorized party outside the allowed clients is refused.
        """
        if "crit" in parsed.header:
            raise exceptions.Unauthenticated("token names a critical header not understood")
        if parsed.algorithm not in (issuer.algorithms or frozenset()):
            raise exceptions.Unauthenticated(
                f"token signed with {parsed.algorithm or 'nothing'}, not accepted"
            )
        key = issuer.keys.key_for(parsed.key_id, parsed.algorithm)
        if key.algorithm != parsed.algorithm or not key.verify(
            parsed.signing_input, parsed.signature
        ):
            raise exceptions.Unauthenticated("token signature does not match")
        issuer.rules().check(parsed.claims, now=self.clock())
        kind = parsed.claims.get("typ")
        if isinstance(kind, str) and kind in issuer.mapping.refused_types:
            raise exceptions.Unauthenticated(f"a {kind} token is not an access token")
        party = parsed.claims.get("azp")
        if issuer.authorized_parties is not None and party not in issuer.authorized_parties:
            raise exceptions.Unauthenticated("token issued to a client not accepted")

    def _not_revoked(self, identity: Identity, parsed: jws.Jws) -> None:
        """`Unauthenticated` when the token, its session or every session of its subject was
        revoked — one read of `revocations`, which also reads the tenant's permission version for
        the resolvers — or when it carries permissions (`pv`) of a version the subject's, its
        tenant's or every tenant's has moved past: they changed, the token must be refreshed.
        """
        if self.revocations is None:
            return
        state = self.revocations.state(
            identity.subject, identity.token_id, identity.session, related_of(identity)
        )
        refusal = state.refusal(jws.moment(parsed.claims, "iat"))
        if refusal is not None:
            raise exceptions.Unauthenticated(refusal)
        carried = parsed.claims.get("pv")
        if isinstance(carried, int) and carried != state.permission_epoch:
            raise exceptions.Unauthenticated("permissions changed: refresh the token")

    def token_of(self, credentials: Credentials) -> str | None:
        """The token of `Authorization: Bearer …` — or `DPoP …` when proofs are checked."""
        scheme, _, token = credentials.headers.get("authorization", "").partition(" ")
        accepted = ("bearer", "dpop") if self.dpop is not None else ("bearer",)
        return token.strip() or None if scheme.lower() in accepted else None

    def _bound(self, credentials: Credentials, token: str, claims: Mapping[str, Any]) -> None:
        """A token bound to a client certificate (RFC 8705, `cnf.x5t#S256`) is accepted only
        over that certificate; one bound to a key only with its proof (RFC 9449, `DpopProofs`).
        """
        confirmation = claims.get("cnf")
        pinned = confirmation.get("x5t#S256") if isinstance(confirmation, dict) else None
        if pinned is not None and (
            credentials.peer_certificate is None
            or jws.certificate_thumbprint(credentials.peer_certificate) != pinned
        ):
            raise exceptions.Unauthenticated("token is bound to another client certificate")
        if self.dpop is not None:
            self.dpop.check(credentials, token, claims)
        elif isinstance(confirmation, dict) and "jkt" in confirmation:
            raise exceptions.Unauthenticated("a DPoP-bound token, and no proofs are checked")

    def authenticate(self, credentials: Credentials) -> Identity | None:
        """Who the bearer JWT says is calling.

            Authorization: Bearer <Keycloak token for billing-api>
                ->  Identity("user:4c1…", roles={...}, scopes={...}, provider="jwt")
            no bearer, an opaque token, an issuer not trusted   ->  None
            a trusted issuer's token, expired                   ->  raises Unauthenticated

        1. No bearer, or one that is not a compact JWS: `None` — an opaque token is
           introspection's.
        2. An `iss` this provider does not trust: `None`.
        3. Verified against that issuer (`_verified`).
        4. Mapped to an identity; refused when revoked.
        Final: the identity through every resolver — which ask no permission version the
        revocation check already read.
        """
        token = self.token_of(credentials)
        if token is None or not jws.looks_like_jws(token):
            return None
        parsed = jws.parse(token)
        issuer = self._issuer_of(parsed)
        if issuer is None:
            return None
        self._verified(parsed, issuer)
        self._bound(credentials, token, parsed.claims)
        identity = issuer.mapping.identity(parsed.claims, provider=self.name)
        with remembering_versions():
            self._not_revoked(identity, parsed)
            return resolved(identity, self.resolvers)
