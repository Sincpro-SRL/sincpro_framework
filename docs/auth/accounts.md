# Accounts the service manages itself

Most services verify a token an identity provider issued ([the providers](server.md)). A small
service with no identity provider — a college's members, a shop's customers — keeps its own
accounts. The framework never ships a user table nor a login screen: the account is the project's
own entity. It ships what every such project otherwise writes by hand, and gets wrong the same
ways: hashing, locking guessed accounts, one-time links, TOTP, and issuing tokens with refresh,
logout and revocation. Every block on this page runs, in order, in
`tests/docs/test_persistence_guide.py`.

## Passwords

```python
from sincpro_framework.auth import PasswordHasher, ScryptCost

hasher = PasswordHasher(scheme="scrypt", scrypt=ScryptCost(ln=12))   # ln=15 in production
stored = hasher.hash("correct horse battery")
assert stored.startswith("$scrypt$ln=12,r=8,p=1$")
assert hasher.verify(stored, "correct horse battery")
assert not hasher.verify(None, "correct horse battery")    # a username nobody holds: as slow, False
assert PasswordHasher(scheme="scrypt").needs_rehash(stored)      # the cost rose: rehash on login
```

| | |
|---|---|
| scheme | `"auto"` — argon2id when `argon2-cffi` is installed (`[argon2]`), else scrypt (standard library); pin it in settings across a fleet |
| a username nobody holds | `verify(None, …)` hashes anyway and answers `False`: the time it takes says nothing |
| the same characters typed differently | NFKC first — `ﬁ` and `fi` are one password |
| a megabyte of input | refused past `max_length` (1024) before it is hashed — `PasswordRejected` |
| an older format | `legacy={"scrypt$": verify_old}` verifies it once more; `needs_rehash` says to store the new hash |

## Locking guessed accounts

```python
from datetime import timedelta

from sincpro_framework.auth import LoginAttempts
from sincpro_framework.data_layer.store import InMemoryKeyValue
from sincpro_framework.rate_limiter import RateLimited

attempts = LoginAttempts(InMemoryKeyValue(), max_failures=3, lock_for=timedelta(minutes=15))
for _ in range(3):
    attempts.failed("ana@colegio.bo")
try:
    attempts.refuse_if_locked("ana@colegio.bo")
except RateLimited as locked:
    assert locked.retry_after <= timedelta(minutes=15)      # 429 with Retry-After, on every wire
```

The count is an atomic increment on the store every replica shares — two failures at once are
two — keyed by the username's digest, so an email never lands in Redis and a name nobody holds is
counted and locked like a real one. A store that does not answer lets the attempt through, logged:
a Redis restart never locks everyone out.

## A login use case

The account is the project's; the use case puts the pieces together — the lock checked before the
password, one answer whatever failed.

```python
from dataclasses import dataclass

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.auth import AccessControl, Identity, Permission, Unauthenticated


@dataclass
class Account:                      # the project's own entity, in its own table
    username: str
    password_hash: str
    active: bool = True


class MembersPermission(Permission):
    PAY_DUES = "members.dues.pay"


accounts = {"MFAQ0123": Account("MFAQ0123", hasher.hash("clave segura 123"))}
members_auth = AccessControl[MembersPermission]()
members = UseFramework("members-login", log_after_execution=False)


class CommandSignIn(DataTransferObject):
    username: str
    password: str


class SignedIn(DataTransferObject):
    subject: str


@members.feature(CommandSignIn)
@members_auth.public
class SignIn(Feature):
    def execute(self, dto: CommandSignIn) -> SignedIn:
        attempts.refuse_if_locked(dto.username)                     # before any hash
        account = accounts.get(dto.username)
        good = hasher.verify(account.password_hash if account else None, dto.password)
        if not good or account is None or not account.active:
            attempts.failed(dto.username)
            raise Unauthenticated("wrong username or password")    # one answer, whatever failed
        attempts.succeeded(dto.username)
        if hasher.needs_rehash(account.password_hash):
            account.password_hash = hasher.hash(dto.password)
        return SignedIn(subject=f"user:{account.username}")


members_auth.on(members)
assert members(CommandSignIn(username="MFAQ0123", password="clave segura 123"), SignedIn).subject == "user:MFAQ0123"
```

What it answers — a token, a session — is the [token issuer](#issuing-tokens)'s.

## HTTP Basic: service accounts

```python
import base64

from sincpro_framework.auth import BasicProvider, Credentials, HashedCredentials

service_accounts = HashedCredentials(
    {"reports": (hasher.hash("reports secret"), Identity.service("reports"))}, hasher
)
basic = BasicProvider(service_accounts, attempts=LoginAttempts(InMemoryKeyValue()))
pair = base64.b64encode(b"reports:reports secret").decode()
assert basic.authenticate(
    Credentials(transport="http", headers={"authorization": f"Basic {pair}"})
).subject == "service:reports"
```

A pair verified is remembered in this process for a minute — by an HMAC under a random key, never
the pair — so a client sending it on every request does not pay a hash each time. Any
`CredentialVerifier` of the project's — its own accounts table — stands in for `HashedCredentials`.
