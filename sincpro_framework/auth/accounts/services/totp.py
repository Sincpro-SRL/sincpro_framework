"""`Totp`: a second factor from an authenticator app — RFC 6238 — on the standard library: a
secret shared once by QR code, a six-digit code that changes every thirty seconds.

    totp = Totp(issuer="CAUOR")                                  the running context's store
    secret = totp.new_secret()                                   kept encrypted with the account
    totp.provisioning_uri(secret, "MFAQ0123")                    the QR code's content
    totp.verify(secret, "492039", "user:MFAQ0123")               ->  True, once

Context: a code is accepted within `window` steps either side (clock skew), compared in constant
time, and once: a step accepted for a subject is never accepted again — nor an older one —
written by compare-and-set on the store every replica shares, so the same code sent twice at
once is accepted once (NIST SP 800-63B: an OTP is used once). Six digits can be guessed: check
each code inside `LoginAttempts.attempt`. The secret cannot be hashed — the server computes codes
from it — so keep it encrypted at rest.
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from collections.abc import Callable
from datetime import timedelta
from enum import StrEnum
from urllib.parse import quote, urlencode

from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.infrastructure.digests import digest
from sincpro_framework.context.entrypoint.resolution import current_store, key_value_store_of
from sincpro_framework.data_layer.store import KeyValueStore


class TotpAlgorithm(StrEnum):
    """The HMAC a code is computed with — what the authenticator app is told in the QR code.
    SHA1 is what every app reads; the others only some."""

    SHA1 = "SHA1"
    SHA256 = "SHA256"
    SHA512 = "SHA512"


ALGORITHMS = {
    TotpAlgorithm.SHA1: hashlib.sha1,
    TotpAlgorithm.SHA256: hashlib.sha256,
    TotpAlgorithm.SHA512: hashlib.sha512,
}


class Totp:
    """Time-based one-time passwords of one issuer."""

    def __init__(
        self,
        issuer: str,
        digits: int = 6,
        period: int = 30,
        algorithm: TotpAlgorithm = TotpAlgorithm.SHA1,
        window: int = 1,
        store: KeyValueStore | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """`window` is how many steps either side of now a code is accepted in — a phone whose
        clock drifts; `store` keeps the last step each subject used — the running context's
        when not given."""
        if algorithm not in ALGORITHMS:
            raise exceptions.ProgrammingError(
                f"TOTP algorithm is one of {[one.value for one in TotpAlgorithm]}"
            )
        self.issuer = issuer
        self.digits = digits
        self.period = period
        self.algorithm = TotpAlgorithm(algorithm)
        self.window = window
        self._store = store
        self.clock = clock

    def store(self) -> KeyValueStore:
        """The store the steps used are kept in — the one given, else the running context's."""
        return self._store if self._store is not None else key_value_store_of(current_store())

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
        """The secret's bytes — base32, as an authenticator app reads it, padding restored."""
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
        recorded as the last, by compare-and-set.

        Context: the record lives as long as a step it refuses could still verify — up to
        `window` steps ahead accepted now, valid `window` steps after it, one more for the
        moment within the step — and is gone after, so the store holds no key per subject
        forever.
        """
        store = self.store()
        key = f"sincpro:auth:totp:{digest(subject)}"
        lives = timedelta(seconds=self.period * (2 * self.window + 2))
        for _ in range(16):
            held = store.get(key)
            if held is not None and int(held) >= step:
                return False
            written = str(step).encode()
            if held is None and store.add(key, written, lives):
                return True
            if held is not None and store.replace(key, held, written, lives):
                return True
        return False

    def verify(self, secret: str, code: str, subject: str) -> bool:
        """Whether `code` is the secret's within `window` steps of now, used by `subject` for
        the first time. A code of anything but ASCII digits is not."""
        if not (code.isascii() and code.isdigit()) or len(code) != self.digits:
            return False
        current = int(self.clock() // self.period)
        for step in range(current - self.window, current + self.window + 1):
            if hmac.compare_digest(self.code_at(secret, step), code):
                return self._first_use(subject, step)
        return False
