"""`RequestSigner`: this service signing the body it sends — a webhook it delivers, an API that
verifies HMAC — in the style `SignatureProvider` verifies.

    RequestSigner(settings.webhook_secret, TimestampedSignature("x-billing-signature"))

Context: the body is read (`needs_body`), so a caller hands it in the `OutgoingRequest`; a
timestamped signature names the moment it was made, from `clock`.
"""

import time
from collections.abc import Callable
from typing import Any

from sincpro_framework.auth.client.domain.outbound import OutgoingRequest, TokenSource
from sincpro_framework.auth.common.infrastructure.digests import reveal_secret
from sincpro_framework.auth.common.infrastructure.signatures import SignatureScheme


class RequestSigner(TokenSource):
    """The signature header of a request's body, with `secret`."""

    needs_body = True

    def __init__(
        self,
        secret: Any,
        scheme: SignatureScheme,
        name: str = "request-signer",
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._secret = secret
        self.scheme = scheme
        self.name = name
        self.clock = clock

    def headers_for(self, request: OutgoingRequest) -> dict[str, str]:
        """The style's header, signing the request's body at this moment."""
        written = self.scheme.sign_header(
            reveal_secret(self._secret).encode(), request.body or b"", int(self.clock())
        )
        return {self.scheme.header: written}

    def __repr__(self) -> str:
        return f"RequestSigner(header={self.scheme.header!r})"
