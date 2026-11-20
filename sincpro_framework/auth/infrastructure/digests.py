"""What a store, a cache key or a log line may hold of a secret: its SHA-256, a few characters of
it, never the secret.

    digest("rt_3f9…")         ->  "a1b2c3…" (64 hex characters) — what a store looks it up by
    fingerprint("eyJhbGci…")   ->  "a1b2c3d4" — what a log line may show
    revealed(Secret("s"))      ->  "s" — read where it is used, never earlier

Context: a token, a refresh token, a one-time link and an API key are long random values, so a
plain hash is enough to find them — no salt, no stretching, one lookup instead of a comparison per
row — and a store that leaks leaks nothing usable. A password is not one of them: it is
low-entropy and goes through `PasswordHasher`.
"""

import hashlib
import secrets
from typing import Any

from pydantic import Secret, SecretBytes, SecretStr


def digest(value: str | bytes) -> str:
    """The SHA-256 of `value`, in hex."""
    raw = value.encode() if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def fingerprint(value: str | bytes) -> str:
    """Eight hex characters of `value`'s SHA-256 — enough to tell two tokens apart in a log,
    nothing to recover either."""
    return digest(value)[:8]


def revealed(value: Any) -> str:
    """The text of a secret given as `str`, `SecretStr` or pydantic `Secret[str]`.

        revealed("s")              ->  "s"
        revealed(SecretStr("s"))   ->  "s"

    Context: components take either, so a project hands what its settings hold — a `Secret`
    read from `$ENV:…` — without unwrapping it first.
    """
    if isinstance(value, (SecretStr, SecretBytes, Secret)):
        value = value.get_secret_value()
    if isinstance(value, bytes):
        return value.decode()
    return str(value)


def random_token(prefix: str = "", length: int = 32) -> str:
    """A URL-safe random token of `length` bytes of entropy, after `prefix`.

        random_token("rt_")   ->  "rt_Q2x9…" (43 characters after the prefix)

    Context: the prefix tells a reader — and a secret scanner — what a leaked value is.
    """
    return f"{prefix}{secrets.token_urlsafe(length)}"
