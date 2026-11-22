"""`PasswordHasher`: a password kept as a slow, salted hash — scrypt from the standard library,
argon2id when `argon2-cffi` is installed (the `[argon2]` extra) — verified in constant time,
rehashed when its cost falls behind.

    hasher = PasswordHasher()                     argon2id if installed, else scrypt
    stored = hasher.hash("correct horse")         "$scrypt$ln=15,r=8,p=1$<salt>$<key>"
    hasher.verify(stored, "correct horse")        ->  True
    hasher.needs_rehash(stored)                   ->  False until the cost is raised
    PasswordHasher(legacy={"scrypt$": quipu_verify})   an older format verified, then rehashed

Context: a password is NFKC-normalized first, so the same characters typed on two keyboards are
the same password. `verify(None, …)` — a username nobody holds — runs a hash all the same and
answers `False`, so the time it takes says nothing about which usernames exist. A password longer
than `max_length` is refused before it is hashed: a megabyte of input would otherwise be a way to
burn the server's CPU. With `HashScheme.AUTO`, pin it in settings across a fleet: a machine without
argon2 cannot verify an argon2 hash.
"""

import base64
import hashlib
import hmac
import secrets
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sincpro_framework.auth.domain import exceptions


class HashScheme(StrEnum):
    """How new passwords are hashed — every scheme is still verified."""

    AUTO = "auto"
    """argon2id when `argon2-cffi` is installed, else scrypt."""
    SCRYPT = "scrypt"
    ARGON2ID = "argon2id"


@dataclass(frozen=True)
class ScryptCost:
    """scrypt's cost — N = 2**ln, r, p (RFC 7914). The default is OWASP's floor."""

    ln: int = 15
    r: int = 8
    p: int = 1


def _argon2() -> Any:
    """argon2-cffi's `PasswordHasher` class — `None` when it is not installed."""
    try:
        from argon2 import PasswordHasher as Argon2Hasher
    except ImportError:
        return None
    return Argon2Hasher


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.b64decode(text + "=" * (-len(text) % 4))


class PasswordHasher:
    """Hashes and verifies passwords in one scheme, and verifies the ones of `legacy` too."""

    def __init__(
        self,
        scheme: HashScheme = HashScheme.AUTO,
        scrypt: ScryptCost = ScryptCost(),
        max_length: int = 1024,
        legacy: Mapping[str, Callable[[str, str], bool]] | None = None,
    ) -> None:
        """`legacy` verifies hashes another system wrote, by the prefix they start with — each
        verified once more, then `needs_rehash` says to store the new one."""
        argon2 = _argon2()
        if scheme == HashScheme.ARGON2ID and argon2 is None:
            raise exceptions.ProgrammingError(
                "argon2id needs argon2-cffi. Install with: pip install sincpro-framework[argon2]"
            )
        self.scheme = (
            HashScheme.ARGON2ID
            if scheme == HashScheme.ARGON2ID or (scheme == HashScheme.AUTO and argon2)
            else HashScheme.SCRYPT
        )
        self.cost = scrypt
        self.max_length = max_length
        self.legacy = dict(legacy or {})
        self._argon2: Any = argon2() if argon2 is not None else None
        self._dummy = self.hash("a password nobody holds")

    def _normalized(self, password: str) -> str:
        """NFKC — `PasswordRejected` past `max_length`."""
        if len(password) > self.max_length:
            raise exceptions.PasswordRejected(
                f"a password is at most {self.max_length} characters"
            )
        return unicodedata.normalize("NFKC", password)

    def _scrypt(self, password: str, salt: bytes, cost: ScryptCost) -> bytes:
        return hashlib.scrypt(
            password.encode(),
            salt=salt,
            n=2**cost.ln,
            r=cost.r,
            p=cost.p,
            dklen=32,
            maxmem=256 * 1024 * 1024,
        )

    def hash(self, password: str) -> str:
        """`password` hashed with a new salt, in this hasher's scheme and cost."""
        normalized = self._normalized(password)
        if self.scheme == HashScheme.ARGON2ID:
            return self._argon2.hash(normalized)
        salt = secrets.token_bytes(16)
        cost = self.cost
        key = self._scrypt(normalized, salt, cost)
        return f"$scrypt$ln={cost.ln},r={cost.r},p={cost.p}${_b64(salt)}${_b64(key)}"

    def _scrypt_parts(self, stored: str) -> tuple[ScryptCost, bytes, bytes] | None:
        """The cost, salt and key of a `$scrypt$` hash — `None` when it is not one."""
        try:
            _, name, params, salt, key = stored.split("$")
            values = dict(one.split("=") for one in params.split(","))
            cost = ScryptCost(int(values["ln"]), int(values["r"]), int(values["p"]))
        except (ValueError, KeyError):
            return None
        return (cost, _unb64(salt), _unb64(key)) if name == "scrypt" else None

    def _legacy_for(self, stored: str) -> Callable[[str, str], bool] | None:
        return next(
            (check for prefix, check in self.legacy.items() if stored.startswith(prefix)),
            None,
        )

    def verify(self, stored: str | None, password: str) -> bool:
        """Whether `password` is the one `stored` was made from — constant time.

        1. Nothing stored: the dummy hash verified, `False` — as long as a real check.
        2. A password past `max_length`: `False`, nothing hashed.
        3. `$scrypt$` and `$argon2…` verified; a `legacy` prefix by its own check.
        Final: `False` for a format nobody here reads.
        """
        if stored is None:
            self.verify(self._dummy, password)
            return False
        try:
            normalized = self._normalized(password)
        except exceptions.PasswordRejected:
            return False
        if stored.startswith("$argon2"):
            if self._argon2 is None:
                raise exceptions.ProgrammingError(
                    "an argon2 hash needs argon2-cffi. Install with: pip install sincpro-framework[argon2]"
                )
            try:
                return bool(self._argon2.verify(stored, normalized))
            except Exception:
                return False
        parts = self._scrypt_parts(stored)
        if parts is not None:
            cost, salt, key = parts
            return hmac.compare_digest(self._scrypt(normalized, salt, cost), key)
        check = self._legacy_for(stored)
        return bool(check and check(stored, password))

    def needs_rehash(self, stored: str) -> bool:
        """Whether `stored` should be replaced by `hash(password)` once the password is known
        good — another scheme, a lower cost, a legacy format."""
        if self.scheme == HashScheme.ARGON2ID:
            if not stored.startswith("$argon2id$"):
                return True
            return bool(self._argon2.check_needs_rehash(stored))
        parts = self._scrypt_parts(stored)
        return parts is None or parts[0] != self.cost
