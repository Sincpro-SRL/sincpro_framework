"""`PrivateKeyJwt`: this service authenticates as an OAuth client with an assertion signed by its
own private key — RFC 7523, `private_key_jwt` — the `[jwt]` extra. No secret is shared: the
authorization server holds the public key, or reads it from the client's JWKS.

    PrivateKeyJwt("billing-api", PrivateKey.from_pem(settings.client_key, "RS256", key_id="k1"))
"""

import time
from collections.abc import Callable, Sequence
from typing import Any

from sincpro_framework.auth.client.adapters.client_authentication import assertion_fields
from sincpro_framework.auth.common.adapters.jose_keys import PrivateKey
from sincpro_framework.auth.common.domain.http import ClientAuthentication
from sincpro_framework.auth.common.infrastructure import jws


class PrivateKeyJwt(ClientAuthentication):
    """`private_key_jwt`: a one-minute assertion for the endpoint, signed with `key`."""

    def __init__(
        self, client_id: str, key: PrivateKey, clock: Callable[[], float] = time.time
    ) -> None:
        self.client_id = client_id
        self.key = key
        self.clock = clock

    def _sign(self, claims: dict[str, Any]) -> str:
        header = {"alg": self.key.algorithm, "typ": "JWT"}
        if self.key.key_id:
            header["kid"] = self.key.key_id
        return jws.encode(header, claims, self.key.sign)

    def applied(
        self, fields: Sequence[tuple[str, str]], endpoint: str
    ) -> tuple[list[tuple[str, str]], dict[str, str]]:
        """The fields with a fresh assertion for `endpoint`."""
        return [
            *fields,
            *assertion_fields(self.client_id, endpoint, self._sign, self.clock),
        ], {}

    def __repr__(self) -> str:
        return f"PrivateKeyJwt(client_id={self.client_id!r}, key_id={self.key.key_id!r})"
