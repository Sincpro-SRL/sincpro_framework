"""`AesGcmSealer`: what a shared store holds of a secret — a token cached on Redis, a held refresh
token — encrypted with AES-256-GCM, so a store that leaks leaks no credential. The `[jwt]` extra
(`cryptography`).

    TokenCache(seal=AesGcmSealer(settings.cache_key))          32 bytes, or their base64
    KeyValueHeldGrants(seal=AesGcmSealer(settings.cache_key))

Context: each value gets a random nonce; one sealed with another key, or changed, does not open —
`ValueError`, which a cache counts as a miss and fetches again.
"""

import base64
import binascii
import secrets
from typing import Any

from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.infrastructure.digests import revealed
from sincpro_framework.auth.infrastructure.jose_keys import JWT_MISSING

try:  # pragma: no cover - tests/core/test_core_without_extras.py
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError as error:  # pragma: no cover
    raise ImportError(JWT_MISSING) from error

VERSION = b"\x01"


class AesGcmSealer:
    """AES-256-GCM with one key — `seal` and `open`."""

    def __init__(self, key: Any) -> None:
        """`key` is 32 bytes, or their base64 as text or a pydantic `Secret`."""
        raw = key if isinstance(key, bytes) else self._decoded(revealed(key))
        if len(raw) != 32:
            raise exceptions.ProgrammingError("AesGcmSealer needs a 32-byte key")
        self._aead = AESGCM(raw)

    @staticmethod
    def _decoded(text: str) -> bytes:
        try:
            return base64.b64decode(text, validate=True)
        except (binascii.Error, ValueError) as error:
            raise exceptions.ProgrammingError("AesGcmSealer's key is not base64") from error

    @staticmethod
    def new_key() -> str:
        """A new key, base64 — to keep as a secret."""
        return base64.b64encode(secrets.token_bytes(32)).decode()

    def seal(self, plain: bytes) -> bytes:
        """A version byte, a random 96-bit nonce and AES-GCM over `plain` — the version
        authenticated with it."""
        nonce = secrets.token_bytes(12)
        return VERSION + nonce + self._aead.encrypt(nonce, plain, VERSION)

    def open(self, sealed: bytes) -> bytes:
        """What `seal` sealed — `ValueError` for another version, another key, or a byte
        changed."""
        if sealed[:1] != VERSION or len(sealed) < 13:
            raise ValueError("not a sealed value")
        try:
            return self._aead.decrypt(sealed[1:13], sealed[13:], VERSION)
        except InvalidTag as error:
            raise ValueError("sealed with another key, or changed") from error
