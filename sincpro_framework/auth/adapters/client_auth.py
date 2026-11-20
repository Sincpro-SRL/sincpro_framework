"""How this service authenticates as an OAuth client to a token, introspection, revocation or
device-authorization endpoint — the strategies of RFC 6749 §2.3 and RFC 7523 that need nothing
but the standard library. `PrivateKeyJwt` signs with a key, and is the `[jwt]` extra's.

    ClientSecretBasic("billing-api", Secret("…"))    Authorization: Basic <id:secret>
    ClientSecretPost("billing-api", Secret("…"))     client_id and client_secret in the form
    ClientSecretJwt("billing-api", Secret("…"))      an HS256 assertion signed with the secret
    PublicClient("cli")                              client_id alone — a CLI, a device

Context: a secret is held as given — text or a pydantic `Secret` — and read only when a request
is made; no `repr` shows it.
"""

import base64
import time
from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import quote

from sincpro_framework.auth.domain.http import ClientAuthentication
from sincpro_framework.auth.infrastructure import jws
from sincpro_framework.auth.infrastructure.digests import random_token, revealed

CLIENT_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"


class ClientSecretBasic(ClientAuthentication):
    """`client_secret_basic`: the id and secret in `Authorization: Basic`, each form-encoded
    before base64 — RFC 6749 §2.3.1, which most clients forget, so a secret with a `:` or a
    `%` fails against a strict server."""

    def __init__(self, client_id: str, client_secret: Any) -> None:
        """A client and its secret, as text or a `Secret`."""
        self.client_id = client_id
        self._secret = client_secret

    def applied(
        self, fields: Sequence[tuple[str, str]], endpoint: str
    ) -> tuple[list[tuple[str, str]], dict[str, str]]:
        """The fields as they are, and the `Authorization` header."""
        pair = f"{quote(self.client_id, safe='')}:{quote(revealed(self._secret), safe='')}"
        encoded = base64.b64encode(pair.encode()).decode()
        return list(fields), {"authorization": f"Basic {encoded}"}

    def __repr__(self) -> str:
        return f"ClientSecretBasic(client_id={self.client_id!r})"


class ClientSecretPost(ClientAuthentication):
    """`client_secret_post`: `client_id` and `client_secret` in the form body."""

    def __init__(self, client_id: str, client_secret: Any) -> None:
        """A client and its secret, as text or a `Secret`."""
        self.client_id = client_id
        self._secret = client_secret

    def applied(
        self, fields: Sequence[tuple[str, str]], endpoint: str
    ) -> tuple[list[tuple[str, str]], dict[str, str]]:
        """The fields with the id and the secret appended; no header."""
        return [
            *fields,
            ("client_id", self.client_id),
            ("client_secret", revealed(self._secret)),
        ], {}

    def __repr__(self) -> str:
        return f"ClientSecretPost(client_id={self.client_id!r})"


class ClientSecretJwt(ClientAuthentication):
    """`client_secret_jwt` (RFC 7523): a one-minute HS256 assertion signed with the secret —
    the secret never travels."""

    def __init__(
        self,
        client_id: str,
        client_secret: Any,
        algorithm: str = "HS256",
        clock: Callable[[], float] = time.time,
    ) -> None:
        """A client, its secret, the HMAC algorithm and the clock its assertions are dated by."""
        self.client_id = client_id
        self._key = jws.HmacKey.of(None, client_secret, algorithm)
        self.clock = clock

    def _sign(self, claims: dict[str, Any]) -> str:
        """`claims` signed as a compact JWS with the secret."""
        return jws.encode({"alg": self._key.algorithm, "typ": "JWT"}, claims, self._key.sign)

    def applied(
        self, fields: Sequence[tuple[str, str]], endpoint: str
    ) -> tuple[list[tuple[str, str]], dict[str, str]]:
        """The fields with `client_assertion_type`, a fresh assertion for `endpoint` and the
        `client_id`."""
        return [
            *fields,
            *assertion_fields(self.client_id, endpoint, self._sign, self.clock),
        ], {}

    def __repr__(self) -> str:
        return f"ClientSecretJwt(client_id={self.client_id!r})"


class PublicClient(ClientAuthentication):
    """A client with no secret — a CLI, a device, a single-page app: `client_id` alone."""

    def __init__(self, client_id: str) -> None:
        self.client_id = client_id

    def applied(
        self, fields: Sequence[tuple[str, str]], endpoint: str
    ) -> tuple[list[tuple[str, str]], dict[str, str]]:
        """The fields with `client_id` appended."""
        return [*fields, ("client_id", self.client_id)], {}

    def __repr__(self) -> str:
        return f"PublicClient(client_id={self.client_id!r})"


def assertion_fields(
    client_id: str,
    endpoint: str,
    sign: Callable[[dict[str, Any]], str],
    clock: Callable[[], float],
) -> list[tuple[str, str]]:
    """The form fields of a JWT client assertion (RFC 7523 §3) for `endpoint`.

    Context: `iss` and `sub` are the client, `aud` the endpoint it is presented to, `jti` a
    random id so a replayed assertion is refused, and it lives a minute.
    """
    now = int(clock())
    claims = {
        "iss": client_id,
        "sub": client_id,
        "aud": endpoint,
        "jti": random_token(length=16),
        "iat": now,
        "exp": now + 60,
    }
    return [
        ("client_assertion_type", CLIENT_ASSERTION_TYPE),
        ("client_assertion", sign(claims)),
        ("client_id", client_id),
    ]
