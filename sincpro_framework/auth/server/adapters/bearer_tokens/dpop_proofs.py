"""`DpopProofs`: a token bound to a key the client holds — RFC 9449 — so a token that leaks is of
no use without the key: each request carries a proof signed with it, for that method and URL,
once. The `[jwt]` extra.

    JwtProvider(keycloak, dpop=DpopProofs("https://billing.sincpro.com.bo"))

Context: a token whose `cnf.jkt` names a key is bound — it is refused as a plain `Bearer`, and
accepted as `DPoP` only with a proof by that key. `htu` is compared to `public_base_url` and the
request's path — never to the Host header, which the client chooses. With `required`, every token
must come as `DPoP`. A proof is a minute long (`max_age`) and, with `replay`, accepted once.
"""

import hashlib
import time
from collections.abc import Callable, Iterable, Mapping
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.domain.identity import Credentials
from sincpro_framework.auth.infrastructure import jws
from sincpro_framework.auth.infrastructure.digests import digest
from sincpro_framework.auth.infrastructure.jose_keys import PublicKey
from sincpro_framework.data_layer.store import KeyValueStore

DEFAULT_ALGORITHMS = ("ES256", "PS256", "EdDSA", "RS256")


def access_token_hash(token: str) -> str:
    """`ath`: the base64url SHA-256 of the access token."""
    return jws.encoded(hashlib.sha256(token.encode()).digest())


class DpopProofs:
    """The proofs a resource server checks."""

    def __init__(
        self,
        public_base_url: str,
        required: bool = False,
        algorithms: Iterable[str] = DEFAULT_ALGORITHMS,
        max_age: timedelta = timedelta(seconds=60),
        replay: KeyValueStore | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.public_base_url = public_base_url.rstrip("/")
        self.required = required
        self.algorithms = frozenset(algorithms)
        self.max_age = max_age
        self.replay = replay
        self.clock = clock

    def challenge(self) -> str:
        """`DPoP algs="…"` — the scheme and the algorithms a proof may be signed with."""
        return f'DPoP algs="{" ".join(sorted(self.algorithms))}"'

    def _expected_htu(self, credentials: Credentials) -> str:
        """The URL the proof must name: the public base and the request's path, no query."""
        path = urlsplit(credentials.uri or "").path
        return f"{self.public_base_url}{path}"

    def _proof_claims(self, proof: str, token: str) -> tuple[Mapping[str, Any], str]:
        """The proof's claims, once its signature by the key it carries is verified — and that
        key's thumbprint."""
        parsed = jws.parse(proof, what="DPoP proof")
        if parsed.header.get("typ") != "dpop+jwt" or parsed.algorithm not in self.algorithms:
            raise exceptions.Unauthenticated("DPoP proof of another type or algorithm")
        jwk = parsed.header.get("jwk")
        if not isinstance(jwk, dict) or "d" in jwk:
            raise exceptions.Unauthenticated("DPoP proof without a public key")
        try:
            key = PublicKey.from_jwk(jwk, parsed.algorithm)
        except exceptions.ProgrammingError as error:
            raise exceptions.Unauthenticated(
                "DPoP proof key does not fit its algorithm"
            ) from error
        if not key.verify(parsed.signing_input, parsed.signature):
            raise exceptions.Unauthenticated("DPoP proof signature does not match")
        return parsed.claims, jws.thumbprint(jwk)

    def check(self, credentials: Credentials, token: str, claims: Mapping[str, Any]) -> None:
        """Refuse the request unless its token's binding and its proof hold.

        1. A bound token (`cnf.jkt`) sent as `Bearer`, or any token as `Bearer` when required.
        2. Sent as `DPoP`: exactly one proof, `dpop+jwt`, signed by the public key it carries.
        3. Its `htm` and `htu` are this request's; its `iat` within `max_age`; its `ath` this
           token's; its `jti` not seen before (with `replay`).
        Final: the proof's key is the one the token is bound to.
        """
        scheme = credentials.headers.get("authorization", "").partition(" ")[0].lower()
        confirmation = claims.get("cnf")
        bound = confirmation.get("jkt") if isinstance(confirmation, dict) else None
        if scheme == "bearer":
            if bound is not None or self.required:
                raise exceptions.Unauthenticated("this token must be sent as DPoP")
            return
        proof = credentials.headers.get("dpop", "")
        if not proof or "," in proof:
            raise exceptions.Unauthenticated("DPoP needs exactly one proof")
        said, key_thumbprint = self._proof_claims(proof, token)
        if said.get("htm") != credentials.method:
            raise exceptions.Unauthenticated("DPoP proof is for another method")
        if str(said.get("htu", "")).split("?")[0] != self._expected_htu(credentials):
            raise exceptions.Unauthenticated("DPoP proof is for another URL")
        issued = jws.numeric_date(said, "iat", "DPoP proof")
        if issued is None or abs(self.clock() - issued) > self.max_age.total_seconds():
            raise exceptions.Unauthenticated("DPoP proof is too old or from the future")
        if said.get("ath") != access_token_hash(token):
            raise exceptions.Unauthenticated("DPoP proof is for another token")
        if self.replay is not None and not self.replay.add(
            f"sincpro:auth:dpop:{digest(str(said.get('jti', '')))}", b"1", self.max_age * 2
        ):
            raise exceptions.Unauthenticated("DPoP proof already used")
        if bound != key_thumbprint:
            raise exceptions.Unauthenticated("DPoP proof is by another key than the token's")
