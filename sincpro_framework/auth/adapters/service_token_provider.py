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

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from typing import Any

from sincpro_framework.auth.domain import (
    AuthProvider,
    Credentials,
    Identity,
    IdentityKind,
    Unauthenticated,
)

HEADER = "x-sp-service-token"
"""Its own header, so a user's `Authorization` travels untouched beside it."""


def _encoded(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _decoded(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class ServiceTokenProvider(AuthProvider):
    def __init__(
        self,
        issuer: str,
        keys: Mapping[str, str],
        audience: str = "sincpro",
        ttl_seconds: int = 60,
        name: str = "service-tokens",
    ) -> None:
        if not keys:
            raise ValueError("ServiceTokenProvider needs at least one key")
        self.issuer = issuer
        self.keys = dict(keys)
        self.signing_key = list(self.keys)[-1]
        self.audience = audience
        self.ttl_seconds = ttl_seconds
        self.name = name

    def _signature(self, key_id: str, signed: str) -> str:
        secret = self.keys[key_id].encode()
        return _encoded(hmac.new(secret, signed.encode(), hashlib.sha256).digest())

    def token_for(self, identity: Identity) -> str:
        now = int(time.time())
        header = {"alg": "HS256", "typ": "JWT", "kid": self.signing_key}
        claims: dict[str, Any] = {
            "iss": self.issuer,
            "aud": self.audience,
            "iat": now,
            "exp": now + self.ttl_seconds,
            "sub": identity.subject,
            "kind": identity.kind.value,
            "tenant": identity.tenant,
            "permissions": sorted(identity.permissions),
            "act": {"sub": f"service:{self.issuer}"},
        }
        signed = ".".join(
            _encoded(json.dumps(part, separators=(",", ":")).encode())
            for part in (header, claims)
        )
        return f"{signed}.{self._signature(self.signing_key, signed)}"

    def _claims(self, token: str) -> dict[str, Any]:
        """1. Three parts, a header naming a key this provider holds and HS256 — `alg` read
           from the header is never trusted to pick another algorithm.
        2. The signature, compared in constant time.
        3. Final: audience and expiry.
        """
        try:
            header_part, claims_part, signature = token.split(".")
            header = json.loads(_decoded(header_part))
            claims = json.loads(_decoded(claims_part))
        except ValueError as error:
            raise Unauthenticated("malformed service token") from error
        key_id = header.get("kid")
        if header.get("alg") != "HS256" or key_id not in self.keys:
            raise Unauthenticated("service token signed with an unknown key")
        expected = self._signature(key_id, f"{header_part}.{claims_part}")
        if not hmac.compare_digest(expected, signature):
            raise Unauthenticated("service token signature does not match")
        if claims.get("aud") != self.audience:
            raise Unauthenticated("service token is for another audience")
        if int(claims.get("exp", 0)) < int(time.time()):
            raise Unauthenticated("service token expired")
        return claims

    def authenticate(self, credentials: Credentials) -> Identity | None:
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
        )

    def security_scheme(self) -> dict[str, Any] | None:
        return {
            "type": "apiKey",
            "in": "header",
            "name": HEADER,
            "description": "A peer Sincpro service acting for an identity — short-lived JWT",
        }

    def credentials_for(self, identity: Identity) -> Credentials | None:
        if identity.is_anonymous or identity.kind == IdentityKind.SYSTEM:
            return None
        return Credentials(transport="service", headers={HEADER: self.token_for(identity)})
