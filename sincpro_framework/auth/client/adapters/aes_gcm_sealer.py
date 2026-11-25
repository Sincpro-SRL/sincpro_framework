"""`AesGcmSealer`: what a shared store holds of a secret — a token cached on Redis, a stored
refresh token — encrypted with AES-256-GCM, so a store that leaks leaks no credential. The `[jwt]`
extra (`cryptography`).

    TokenCache(sealer=AesGcmSealer(settings.cache_key))          32 bytes, or their base64
    KeyValueOAuthGrants(sealer=AesGcmSealer(settings.cache_key))

Context: each value gets a random nonce; one encrypted with another key, or changed, does not decrypt —
`ValueError`, which a cache counts as a miss and fetches again.
"""

import base64
import binascii
import secrets
from typing import Any

from sincpro_framework.auth.common.adapters.jose_keys import JWT_MISSING
from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.infrastructure.digests import reveal_secret

try:  # pragma: no cover - tests/core/test_core_without_extras.py
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError as error:  # pragma: no cover
    raise ImportError(JWT_MISSING) from error

VERSION = b"\x01"


class AesGcmSealer:
    """AES-256-GCM with one key — `encrypt` and `decrypt`."""

    def __init__(self, key: Any) -> None:
        """`key` is 32 bytes, or their base64 as text or a pydantic `Secret`."""
        raw = key if isinstance(key, bytes) else self._decode_key(reveal_secret(key))
        if len(raw) != 32:
            raise exceptions.ProgrammingError("AesGcmSealer needs a 32-byte key")
        self._aead = AESGCM(raw)

    @staticmethod
    def _decode_key(text: str) -> bytes:
        try:
            return base64.b64decode(text, validate=True)
        except (binascii.Error, ValueError) as error:
            raise exceptions.ProgrammingError("AesGcmSealer's key is not base64") from error

    @staticmethod
    def new_key() -> str:
        """A new key, base64 — to store as a secret."""
        return base64.b64encode(secrets.token_bytes(32)).decode()

    def encrypt(self, plain: bytes) -> bytes:
        """A version byte, a random 96-bit nonce and AES-GCM over `plain` — the version
        authenticated with it."""
        nonce = secrets.token_bytes(12)
        return VERSION + nonce + self._aead.encrypt(nonce, plain, VERSION)

    def decrypt(self, sealed: bytes) -> bytes:
        """What `encrypt` encrypted — `ValueError` for another version, another key, or a byte
        changed."""
        if sealed[:1] != VERSION or len(sealed) < 13:
            raise ValueError("not an encrypted value")
        try:
            return self._aead.decrypt(sealed[1:13], sealed[13:], VERSION)
        except InvalidTag as error:
            raise ValueError("encrypted with another key, or changed") from error
