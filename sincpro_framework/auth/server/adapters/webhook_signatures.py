"""`SignatureProvider`: a webhook — a bank, a payment gateway, GitHub — that proves it sent the
request by signing its body with a secret both share.

    payments = SignatureProvider(
        secrets={"2026-09": settings.gateway_secret},
        style=TimestampedSignature("x-gateway-signature"),
        subject="service:payment-gateway",
        permissions={PaymentPermission.NOTIFY},
        replay=KeyValueRevocations-like store or None,
    )

Context: it reads the raw body (`needs_body`), so each wire hands it the bytes as they came — only
when a provider of the bus asks for them. A timestamped signature older than `tolerance` is
refused; with `replay`, a signature is accepted once within it. Several secrets verify, so the
sender rotates without a window where its calls fail. What it vouches for is a SERVICE identity:
the sender, with the permissions given here.
"""

import time
from collections.abc import Callable, Iterable, Mapping
from datetime import timedelta
from typing import Any

from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.domain.identity import Credentials, Identity, IdentityKind
from sincpro_framework.auth.common.domain.provider import AuthProvider
from sincpro_framework.auth.common.infrastructure.digests import digest, revealed
from sincpro_framework.auth.common.infrastructure.signatures import SignatureStyle
from sincpro_framework.data_layer.store import KeyValueStore


class SignatureProvider(AuthProvider):
    """Requests signed with one of `secrets` in `style`, vouched for as `subject`."""

    needs_body = True

    def __init__(
        self,
        secrets: Mapping[str, Any],
        style: SignatureStyle,
        subject: str,
        tenant: str | None = None,
        permissions: Iterable[str] = (),
        tolerance: timedelta = timedelta(minutes=5),
        replay: KeyValueStore | None = None,
        name: str = "webhook",
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not secrets:
            raise exceptions.ProgrammingError("SignatureProvider needs at least one secret")
        self._secrets = [revealed(one).encode() for one in secrets.values()]
        self.style = style
        self.subject = subject
        self.tenant = tenant
        self.permissions = frozenset(permissions)
        self.tolerance = tolerance
        self.replay = replay
        self.name = name
        self.clock = clock

    def security_scheme(self) -> dict[str, Any] | None:
        """The signature header, as an API key in OpenAPI's terms — the nearest it has."""
        return {
            "type": "apiKey",
            "in": "header",
            "name": self.style.header,
            "description": "an HMAC-SHA256 signature of the body",
        }

    def authenticate(self, credentials: Credentials) -> Identity | None:
        """The sender, when the request carries its signature.

        1. No signature header: `None`.
        2. No body handed: `ProgrammingError` — a wire that did not read it.
        3. A timestamp outside `tolerance`: `Unauthenticated`.
        4. No secret's signature among those carried: `Unauthenticated`.
        5. With `replay`, a signature seen within `tolerance`: `Unauthenticated`.
        Final: a SERVICE identity of `subject`.
        """
        value = credentials.headers.get(self.style.header)
        if value is None:
            return None
        if credentials.body is None:
            raise exceptions.ProgrammingError(
                f"{self.name} verifies the body: the entrypoint handed none"
            )
        _, timestamp = self.style.read(value)
        if (
            timestamp is not None
            and abs(self.clock() - timestamp) > self.tolerance.total_seconds()
        ):
            raise exceptions.Unauthenticated("signature outside its tolerance")
        if not any(
            self.style.matches(secret, credentials.body, value) for secret in self._secrets
        ):
            raise exceptions.Unauthenticated("signature does not match")
        if self.replay is not None and not self.replay.add(
            f"sincpro:auth:signature:{digest(value)}", b"1", self.tolerance
        ):
            raise exceptions.Unauthenticated("signature already used")
        return Identity(
            subject=self.subject,
            kind=IdentityKind.SERVICE,
            tenant=self.tenant,
            permissions=self.permissions,
            provider=self.name,
        )
