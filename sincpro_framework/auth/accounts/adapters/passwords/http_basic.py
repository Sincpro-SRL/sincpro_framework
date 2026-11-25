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
from contextlib import nullcontext
from datetime import timedelta
from typing import Any

from sincpro_framework.auth.accounts.adapters.passwords.hasher import PasswordHasher
from sincpro_framework.auth.accounts.adapters.passwords.login_attempts import LoginAttempts
from sincpro_framework.auth.accounts.domain.credential_verifier import CredentialVerifier
from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.domain.identity import Credentials, Identity
from sincpro_framework.auth.common.domain.provider import AuthProvider
from sincpro_framework.auth.common.domain.resolver import IdentityResolver, resolved
from sincpro_framework.rate_limiter import RateLimited


class HashedCredentials(CredentialVerifier):
    """Usernames with their password hash and the identity they stand for — a few service
    accounts kept in settings."""

    def __init__(
        self, entries: Mapping[str, tuple[str, Identity]], hasher: PasswordHasher
    ) -> None:
        self.entries = dict(entries)
        self.hasher = hasher

    def verify(self, username: str, password: str) -> Identity | None:
        """The identity of `username` when `password` is its own — the hasher verifies a dummy
        hash for a name it does not know, so a missing account answers as slowly as a wrong
        password."""
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
        """HTTP Basic (RFC 7617)."""
        return {"type": "http", "scheme": "basic"}

    def challenge(self) -> str | None:
        """`Basic realm=…, charset="UTF-8"` — the prompt, and the password read as UTF-8 (RFC
        7617 §2.1)."""
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

    def _verified(self, username: str, password: str) -> Identity:
        """The identity the verifier answers for the pair — `Unauthenticated` when it answers
        none, or the username is locked: an attempt counted before the hash, so pairs sent at
        once never pass the limit together."""
        counting = (
            self.attempts.attempt(username) if self.attempts is not None else nullcontext()
        )
        try:
            with counting:
                found = self.verifier.verify(username, password)
                if found is None:
                    raise exceptions.Unauthenticated("wrong username or password")
        except RateLimited as locked:
            raise exceptions.Unauthenticated("too many failed attempts") from locked
        return found

    def _remember(self, memo: str, identity: Identity) -> None:
        """`identity` remembered under `memo` for `remember_for` — the pairs past theirs
        forgotten, so the memory holds a minute of pairs, never every pair ever sent."""
        now = self.clock()
        with self._lock:
            self._remembered = {
                one: held for one, held in self._remembered.items() if held[1] > now
            }
            self._remembered[memo] = (identity, now + self.remember_for.total_seconds())

    def authenticate(self, credentials: Credentials) -> Identity | None:
        """Who the Basic pair belongs to.

        1. Another scheme: `None`.
        2. Remembered and not past `remember_for`: that identity.
        3. Verified, the attempt counted first: a locked username is refused before any hash,
           a wrong pair counted and refused — 401 both, with the Basic challenge.
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
        found = self._verified(username, password)
        identity = resolved(found.model_copy(update={"provider": self.name}), self.resolvers)
        self._remember(memo, identity)
        return identity
