"""`ServiceTokenProvider`: one Sincpro service telling another who a call acts for — a short
JWT, signed with a key both share, issued by the caller and verified by the host.

    tokens = ServiceTokenProvider(issuer="billing", keys={"2026-09": secret}, audience="sincpro")
    auth = AccessControl(providers=[keycloak, tokens])      # keycloak for users, tokens for peers

Context: RFC 7519 with HS256 — base64url and HMAC-SHA256, the standard library's — so any
service, in any language, reads what one of these issues. `sub` is the identity the call acts
for, `act` the service acting for it (RFC 8693's shape), `aud` who may accept it, `exp` a minute
away: a token that leaks is useless almost at once. Only the identity's subject, kind, tenant and
permissions travel — never its claims, which may hold what must not leave the process. `keys`
are by id (`kid`), the newest signs and every one verifies, so a key rotates without a window
where calls fail.
"""

import time
from collections.abc import Callable, Mapping
from typing import Any

from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.domain.identity import Credentials, Identity, IdentityKind
from sincpro_framework.auth.domain.provider import AuthProvider
from sincpro_framework.auth.infrastructure import jws
from sincpro_framework.auth.infrastructure.digests import random_token

HEADER = "x-sp-service-token"
"""Its own header, so a user's `Authorization` travels untouched beside it."""


class ServiceTokenProvider(AuthProvider):
    """An `AuthProvider` that both issues the service token a call carries out and verifies
    the one a call carries in.

    Context: the same instance sits on both sides — `credentials_for` when this service calls
    a peer, `authenticate` when a peer calls it — so the two agree on the keys and audience.
    """

    def __init__(
        self,
        issuer: str,
        keys: Mapping[str, Any],
        audience: str = "sincpro",
        ttl_seconds: int = 60,
        name: str = "service-tokens",
        clock: Callable[[], float] = time.time,
    ) -> None:
        """A provider issuing as `issuer` for `audience`, with tokens alive `ttl_seconds`.

        Context: the last key of `keys` signs — the newest, by insertion order — and every key
        verifies; rotating is adding a key at the end and dropping the oldest once no token
        signed with it can still be alive. A key is text or a pydantic `Secret`. No key at all
        is a `ValueError`, at construction. `clock` answers seconds since the epoch — a test's.

            ServiceTokenProvider("billing", keys={"2026-08": old, "2026-09": new})
                ->  signing_key == "2026-09"; both keys verify
            ServiceTokenProvider("billing", keys={})
                ->  raises ValueError("ServiceTokenProvider needs at least one key")
        """
        if not keys:
            raise ValueError("ServiceTokenProvider needs at least one key")
        self.issuer = issuer
        self.keys = {
            key_id: jws.HmacKey.of(key_id, secret) for key_id, secret in keys.items()
        }
        self.signing_key = list(self.keys)[-1]
        self.audience = audience
        self.ttl_seconds = ttl_seconds
        self.name = name
        self.clock = clock
        self._rules = jws.ClaimRules(
            audiences=frozenset({audience}),
            leeway=0,
            required=frozenset({"exp", "sub", "iss"}),
            what="service token",
        )

    def token_for(self, identity: Identity) -> str:
        """A signed JWT saying this service acts for `identity`, valid for `ttl_seconds`.

            tokens.token_for(Identity.user("user:42", tenant="bo", permissions={"a.b.c"}))
                ->  "eyJhbGciOiJIUzI1NiIs….eyJpc3MiOiJiaWxsaW5nIi….3q2-…"

        1. The header names HS256 and the signing key's id (`kid`).
        2. The claims: issuer, audience, issued-at, expiry `now + ttl_seconds`, a token id
           (`jti`), the identity's subject, kind, tenant and sorted permissions, and `act`
           naming this service.
        Final: header and claims as compact JSON, base64url, signed with the newest key.
        """
        now = int(self.clock())
        key = self.keys[self.signing_key]
        header = {"alg": key.algorithm, "typ": "JWT", "kid": self.signing_key}
        claims: dict[str, Any] = {
            "iss": self.issuer,
            "aud": self.audience,
            "iat": now,
            "exp": now + self.ttl_seconds,
            "jti": random_token(length=12),
            "sub": identity.subject,
            "kind": identity.kind.value,
            "tenant": identity.tenant,
            "permissions": sorted(identity.permissions),
            "act": {"sub": f"service:{self.issuer}"},
        }
        return jws.encode(header, claims, key.sign)

    def _claims(self, token: str) -> Mapping[str, Any]:
        """The claims of `token` once it is proven genuine, current and for this audience —
        `Unauthenticated` with the reason otherwise.

        1. A compact JWS whose header names a key this provider holds — `alg` read from the
           header is never trusted to pick another algorithm: the key's own must match.
        2. The signature, compared in constant time.
        Final: the audience, the expiry and the claims every token of these carries.
        """
        parsed = jws.parse(token, what="service token")
        key = self.keys.get(parsed.key_id or "")
        if key is None or parsed.algorithm != key.algorithm:
            raise exceptions.Unauthenticated("service token signed with an unknown key")
        if not key.verifies(parsed):
            raise exceptions.Unauthenticated("service token signature does not match")
        self._rules.check(parsed.claims, now=self.clock())
        return parsed.claims

    def authenticate(self, credentials: Credentials) -> Identity | None:
        """The identity the peer acts for, read from the `x-sp-service-token` header.

        Context: no header is `None` — a user's call, for the next provider. A token that does
        not verify is `Unauthenticated`. The identity is rebuilt from the claims, with the
        peer as its `actor` and this provider as the one that vouched — so its permissions are
        the ones the issuer resolved, not resolved again here.

            x-sp-service-token: <billing's token for user:42>
                ->  Identity(subject="user:42", tenant="bo", provider="service-tokens",
                             actor=Identity.service("billing"))
            no x-sp-service-token header      ->  None — the next provider is tried
            a token signed with an unknown key  ->  raises Unauthenticated

        Only the issuer's claims are trusted, once `_claims` proved them genuine.
        """
        token = credentials.headers.get(HEADER)
        if token is None:
            return None
        claims = self._claims(token)
        return Identity(
            subject=claims["sub"],
            kind=IdentityKind(claims.get("kind", IdentityKind.USER.value)),
            tenant=claims.get("tenant"),
            permissions=frozenset(claims.get("permissions", ())),
            provider=self.name,
            actor=Identity.service(claims["iss"]),
            token_id=claims.get("jti"),
            expires_at=jws.moment(claims, "exp"),
        )

    def security_scheme(self) -> dict[str, Any] | None:
        """An `apiKey` in the `x-sp-service-token` header, said to be a short-lived JWT."""
        return {
            "type": "apiKey",
            "in": "header",
            "name": HEADER,
            "description": "A peer Sincpro service acting for an identity — short-lived JWT",
        }

    def credentials_for(self, identity: Identity) -> Credentials | None:
        """The header carrying a fresh token for `identity`, for the call to a peer.

        Context: nobody and the system are not vouched for — `None`, the peer sees the call as
        the service's own; a token every call, so it never outlives `ttl_seconds`.
        """
        if identity.is_anonymous or identity.kind == IdentityKind.SYSTEM:
            return None
        return Credentials(transport="service", headers={HEADER: self.token_for(identity)})
