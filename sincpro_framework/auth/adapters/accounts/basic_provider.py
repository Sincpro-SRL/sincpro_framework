"""`BasicProvider`: a username and a password in `Authorization: Basic` (RFC 7617) — a service
account, an integration, a small project with no identity provider — checked by a
`CredentialVerifier`, locked after too many failures.

    BasicProvider(HashedCredentials({"reports": (hasher.hash(secret), Identity.service("reports"))},
                                    hasher),
                  attempts=LoginAttempts())

Context: a password hash is slow on purpose, so a pair verified is remembered in this process for
`remember_for` (a minute) — by an HMAC of the pair under a random key of this process, never the
pair — and a client sending it on every request does not pay a hash each time. A locked username
is refused before its password is hashed; a wrong pair is one answer whichever part was wrong.
"""

import base64
import binascii
import hashlib
import hmac
import secrets
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import timedelta
from typing import Any

from sincpro_framework.auth.adapters.accounts.login_attempts import LoginAttempts
from sincpro_framework.auth.adapters.accounts.passwords import PasswordHasher
from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.domain.accounts import CredentialVerifier
from sincpro_framework.auth.domain.identity import Credentials, Identity
from sincpro_framework.auth.domain.provider import AuthProvider
from sincpro_framework.auth.domain.resolver import IdentityResolver, resolved


class HashedCredentials(CredentialVerifier):
    """Usernames with their password hash and the identity they stand for — a few service
    accounts kept in settings."""

    def __init__(
        self, entries: Mapping[str, tuple[str, Identity]], hasher: PasswordHasher
    ) -> None:
        self.entries = dict(entries)
        self.hasher = hasher

    def verify(self, username: str, password: str) -> Identity | None:
        stored, identity = self.entries.get(username, (None, None))
        return identity if self.hasher.verify(stored, password) else None


class BasicProvider(AuthProvider):
    """HTTP Basic credentials, verified, remembered for a minute, locked when guessed at."""

    def __init__(
        self,
        verifier: CredentialVerifier,
        attempts: LoginAttempts | None = None,
        realm: str = "sincpro",
        remember_for: timedelta = timedelta(seconds=60),
        resolvers: Sequence[IdentityResolver] = (),
        name: str = "basic",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.verifier = verifier
        self.attempts = attempts
        self.realm = realm
        self.remember_for = remember_for
        self.resolvers = tuple(resolvers)
        self.name = name
        self.clock = clock
        self._key = secrets.token_bytes(32)
        self._remembered: dict[str, tuple[Identity, float]] = {}
        self._lock = threading.Lock()

    def security_scheme(self) -> dict[str, Any] | None:
        return {"type": "http", "scheme": "basic"}

    def challenge(self) -> str | None:
        return f'Basic realm="{self.realm}", charset="UTF-8"'

    def _pair(self, credentials: Credentials) -> tuple[str, str] | None:
        """The username and password of `Authorization: Basic` — `None` for another scheme,
        `Unauthenticated` for one that does not decode."""
        scheme, _, encoded = credentials.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "basic":
            return None
        try:
            username, colon, password = (
                base64.b64decode(encoded.strip(), validate=True).decode().partition(":")
            )
        except (binascii.Error, UnicodeDecodeError) as error:
            raise exceptions.Unauthenticated("malformed basic credentials") from error
        if not colon:
            raise exceptions.Unauthenticated("malformed basic credentials")
        return username, password

    def _memo(self, username: str, password: str) -> str:
        return hmac.new(
            self._key, f"{username}\x00{password}".encode(), hashlib.sha256
        ).hexdigest()

    def authenticate(self, credentials: Credentials) -> Identity | None:
        """Who the Basic pair belongs to.

        1. Another scheme: `None`.
        2. Remembered and not past `remember_for`: that identity.
        3. A locked username: `Unauthenticated("too many failed attempts")`, nothing hashed.
        4. Verified; a wrong pair counted as a failure and refused.
        Final: the identity, resolved and remembered.
        """
        pair = self._pair(credentials)
        if pair is None:
            return None
        username, password = pair
        memo = self._memo(username, password)
        with self._lock:
            known = self._remembered.get(memo)
        if known is not None and self.clock() < known[1]:
            return known[0]
        if self.attempts is not None and self.attempts.locked_for(username) is not None:
            raise exceptions.Unauthenticated("too many failed attempts")
        found = self.verifier.verify(username, password)
        if found is None:
            if self.attempts is not None:
                self.attempts.failed(username)
            raise exceptions.Unauthenticated("wrong username or password")
        if self.attempts is not None:
            self.attempts.succeeded(username)
        identity = resolved(found.model_copy(update={"provider": self.name}), self.resolvers)
        with self._lock:
            self._remembered[memo] = (
                identity,
                self.clock() + self.remember_for.total_seconds(),
            )
        return identity
