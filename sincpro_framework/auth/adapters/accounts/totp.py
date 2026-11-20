"""`Totp`: a second factor from an authenticator app — RFC 6238 — on the standard library: a
secret shared once by QR code, a six-digit code that changes every thirty seconds.

    totp = Totp(issuer="CAUOR")
    secret = totp.new_secret()                                   kept encrypted with the account
    totp.provisioning_uri(secret, "MFAQ0123")                    the QR code's content
    totp.verify(secret, "492039", subject="user:MFAQ0123")       ->  True, once

Context: a code is accepted within `window` steps either side (clock skew), compared in constant
time; with `replay`, a step accepted for a subject is never accepted again — nor an older one —
written by compare-and-set, so the same code sent twice at once is accepted once. Six digits can
be guessed: count failed codes with `LoginAttempts`. The secret cannot be hashed — the server
computes codes from it — so keep it encrypted at rest.
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from collections.abc import Callable
from urllib.parse import quote, urlencode

from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.infrastructure.digests import digest
from sincpro_framework.data_layer.store import KeyValueStore

ALGORITHMS = {"SHA1": hashlib.sha1, "SHA256": hashlib.sha256, "SHA512": hashlib.sha512}


class Totp:
    """Time-based one-time passwords of one issuer."""

    def __init__(
        self,
        issuer: str,
        digits: int = 6,
        period: int = 30,
        algorithm: str = "SHA1",
        window: int = 1,
        replay: KeyValueStore | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if algorithm not in ALGORITHMS:
            raise exceptions.ProgrammingError(
                f"TOTP algorithm is one of {sorted(ALGORITHMS)}"
            )
        self.issuer = issuer
        self.digits = digits
        self.period = period
        self.algorithm = algorithm
        self.window = window
        self.replay = replay
        self.clock = clock

    def new_secret(self, length: int = 20) -> str:
        """A new secret, base32 — what the authenticator app and the account keep."""
        return base64.b32encode(secrets.token_bytes(length)).decode().rstrip("=")

    def provisioning_uri(self, secret: str, account: str) -> str:
        """The `otpauth://` URI a QR code carries to the authenticator app."""
        label = quote(f"{self.issuer}:{account}")
        query = urlencode(
            {
                "secret": secret,
                "issuer": self.issuer,
                "algorithm": self.algorithm,
                "digits": self.digits,
                "period": self.period,
            }
        )
        return f"otpauth://totp/{label}?{query}"

    def _key(self, secret: str) -> bytes:
        return base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))

    def code_at(self, secret: str, step: int) -> str:
        """The code of time step `step` (RFC 4226's HOTP of the step)."""
        mac = hmac.new(
            self._key(secret), struct.pack(">Q", step), ALGORITHMS[self.algorithm]
        ).digest()
        offset = mac[-1] & 0x0F
        number = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
        return str(number % 10**self.digits).zfill(self.digits)

    def now(self, secret: str) -> str:
        """The code of the current step."""
        return self.code_at(secret, int(self.clock() // self.period))

    def _first_use(self, subject: str, step: int) -> bool:
        """Whether `step` is later than the last step accepted for `subject` — and, when it is,
        recorded as the last, by compare-and-set."""
        assert self.replay is not None
        key = f"sincpro:auth:totp:{digest(subject)}"
        for _ in range(16):
            held = self.replay.get(key)
            if held is not None and int(held) >= step:
                return False
            written = str(step).encode()
            if held is None and self.replay.add(key, written):
                return True
            if held is not None and self.replay.replace(key, held, written):
                return True
        return False

    def verify(self, secret: str, code: str, subject: str | None = None) -> bool:
        """Whether `code` is the secret's within `window` steps of now — and, with `replay` and a
        `subject`, the first use of that step."""
        if not code.isdigit() or len(code) != self.digits:
            return False
        current = int(self.clock() // self.period)
        for step in range(current - self.window, current + self.window + 1):
            if hmac.compare_digest(self.code_at(secret, step), code):
                if self.replay is not None and subject is not None:
                    return self._first_use(subject, step)
                return True
        return False
