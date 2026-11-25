"""`DpopProver`: this service holding the key its tokens are bound to — RFC 9449 — and proving it on
every request, so a token that leaks from a log or a proxy is of no use. The `[jwt]` extra.

    bank = ClientCredentials(..., dpop=DpopProver())       a new ES256 key for this process
    bank.headers_for(request)  ->  {"authorization": "DPoP …", "dpop": "<proof for this request>"}

Context: a proof names the method and URL (no query), when it was made, a random id, and the
token's hash; a server that answers with a `DPoP-Nonce` is told it on the next proof — learnt per
origin from the answer (`learn`), which `ClientCredentials.invalidate` does on a 401.
"""

import hashlib
import threading
import time
from collections.abc import Callable, Mapping
from urllib.parse import urlsplit, urlunsplit

from sincpro_framework.auth.common.adapters.jose_keys import PrivateKey
from sincpro_framework.auth.common.infrastructure import jws
from sincpro_framework.auth.common.infrastructure.digests import random_token


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


class DpopProver:
    """A key and the proofs it signs."""

    def __init__(
        self,
        key: PrivateKey | None = None,
        algorithm: str = "ES256",
        clock: Callable[[], float] = time.time,
    ) -> None:
        """`key` held by this process — a new one for `algorithm` when not given."""
        self.key = key or PrivateKey.generate(algorithm)
        public = self.key.jwk()
        self.jwk = {
            name: value for name, value in public.items() if name not in ("kid", "use", "alg")
        }
        self.thumbprint = jws.thumbprint(self.jwk)
        self.clock = clock
        self._nonces: dict[str, str] = {}
        self._lock = threading.Lock()

    def proof(self, method: str, url: str, access_token: str | None = None) -> str:
        """A proof for one request — `ath` naming the token when it carries one."""
        parts = urlsplit(url)
        claims: dict[str, object] = {
            "jti": random_token(length=16),
            "htm": method.upper(),
            "htu": urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")),
            "iat": int(self.clock()),
        }
        if access_token is not None:
            claims["ath"] = jws.encoded(hashlib.sha256(access_token.encode()).digest())
        with self._lock:
            nonce = self._nonces.get(_origin(url))
        if nonce is not None:
            claims["nonce"] = nonce
        header = {"typ": "dpop+jwt", "alg": self.key.algorithm, "jwk": self.jwk}
        return jws.encode(header, claims, self.key.sign)

    def learn(self, url: str, headers: Mapping[str, str]) -> bool:
        """Remember the `DPoP-Nonce` an answer from `url`'s origin carried — `True` when it is a
        new one, so the request is worth sending again."""
        nonce = {name.lower(): value for name, value in headers.items()}.get("dpop-nonce")
        if not nonce:
            return False
        with self._lock:
            known = self._nonces.get(_origin(url))
            self._nonces[_origin(url)] = nonce
        return known != nonce
