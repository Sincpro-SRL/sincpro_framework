"""HMAC signatures over a request's body — written once for both sides: `RequestSigner` signs what
this service sends, `SignatureProvider` verifies what a webhook sends it.

    HexDigest()                    x-hub-signature-256: sha256=<hex>         GitHub
    TimestampedSignature()         x-signature: t=<unix>,v1=<hex of "t.body">   Stripe's shape

Context: a timestamped signature covers its moment, so a captured request replayed later is
refused once past the tolerance; a plain digest covers the body alone. Several `v1=` values are
accepted — a sender rotating its secret signs with both — and every comparison is in constant
time.
"""

import hashlib
import hmac
from abc import ABC, abstractmethod


def hmac_sha256(secret: bytes, signed: bytes) -> bytes:
    """The HMAC-SHA256 of `signed` with `secret`."""
    return hmac.new(secret, signed, hashlib.sha256).digest()


class SignatureStyle(ABC):
    """How a signature is written in a header, and what it covers."""

    header: str

    @abstractmethod
    def written(self, secret: bytes, body: bytes, timestamp: int) -> str:
        """The header's value signing `body` at `timestamp`."""

    @abstractmethod
    def read(self, value: str) -> tuple[list[bytes], int | None]:
        """The signatures a header's value carries, and the timestamp it names — `([], None)`
        for a value that is not this style's."""

    @abstractmethod
    def covered(self, body: bytes, timestamp: int | None) -> bytes:
        """What a signature covers."""

    def matches(self, secret: bytes, body: bytes, value: str) -> bool:
        """Whether one of the signatures in `value` is `secret`'s over the body."""
        signatures, timestamp = self.read(value)
        expected = hmac_sha256(secret, self.covered(body, timestamp))
        return any(hmac.compare_digest(expected, one) for one in signatures)


class HexDigest(SignatureStyle):
    """`<prefix><hex>` of the body alone — GitHub's `X-Hub-Signature-256: sha256=…`."""

    def __init__(self, header: str = "x-hub-signature-256", prefix: str = "sha256=") -> None:
        self.header = header.lower()
        self.prefix = prefix

    def written(self, secret: bytes, body: bytes, timestamp: int) -> str:
        return f"{self.prefix}{hmac_sha256(secret, body).hex()}"

    def read(self, value: str) -> tuple[list[bytes], int | None]:
        if not value.startswith(self.prefix):
            return [], None
        try:
            return [bytes.fromhex(value[len(self.prefix) :])], None
        except ValueError:
            return [], None

    def covered(self, body: bytes, timestamp: int | None) -> bytes:
        return body


class TimestampedSignature(SignatureStyle):
    """`t=<unix>,v1=<hex>` of `"<t>." + body` — Stripe's shape: the moment is signed too."""

    def __init__(self, header: str = "x-signature") -> None:
        self.header = header.lower()

    def written(self, secret: bytes, body: bytes, timestamp: int) -> str:
        return f"t={timestamp},v1={hmac_sha256(secret, self.covered(body, timestamp)).hex()}"

    def read(self, value: str) -> tuple[list[bytes], int | None]:
        timestamp: int | None = None
        signatures: list[bytes] = []
        for part in value.split(","):
            name, _, said = part.strip().partition("=")
            try:
                if name == "t":
                    timestamp = int(said)
                elif name == "v1":
                    signatures.append(bytes.fromhex(said))
            except ValueError:
                return [], None
        return signatures, timestamp

    def covered(self, body: bytes, timestamp: int | None) -> bytes:
        return f"{timestamp}.".encode() + body
