# Accounts the service manages itself — `sincpro_framework.auth.accounts`

**Door**: `from sincpro_framework.auth.accounts import …` — folder `auth/accounts/`. It builds on
[`auth.server`](server.md): the token issuer verifies what it issued with a `JwtProvider`.

Most services verify a token an identity provider issued ([the providers](server.md)). A small
service with no identity provider — a college's members, a shop's customers — keeps its own
accounts. The framework never ships a user table nor a login screen: the account is the project's
own entity. It ships what every such project otherwise writes by hand, and gets wrong the same
ways: hashing, locking guessed accounts, one-time links, TOTP, and issuing tokens with refresh,
logout and revocation. Every block on this page runs, in order, in
`tests/docs/test_persistence_guide.py`.

## Passwords

```python
from sincpro_framework.auth.accounts import HashScheme, PasswordHasher, ScryptCost

hasher = PasswordHasher(scheme=HashScheme.SCRYPT, scrypt=ScryptCost(ln=12))   # ln=15 in production
stored = hasher.hash("correct horse battery")
assert stored.startswith("$scrypt$ln=12,r=8,p=1$")
assert hasher.verify(stored, "correct horse battery")
assert not hasher.verify(None, "correct horse battery")    # a username nobody holds: as slow, False
assert PasswordHasher(scheme=HashScheme.SCRYPT).needs_rehash(stored)      # the cost rose: rehash on login
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

from sincpro_framework.auth import Unauthenticated
from sincpro_framework.auth.accounts import LoginAttempts
from sincpro_framework.data_layer.store import InMemoryKeyValue
from sincpro_framework.rate_limiter import RateLimited

attempts = LoginAttempts(InMemoryKeyValue(), max_failures=3, lock_for=timedelta(minutes=15))
for _ in range(3):
    try:
        with attempts.attempt("ana@colegio.bo"):            # its turn: one attempt at a time
            raise Unauthenticated("wrong username or password")
    except Unauthenticated:
        pass
try:
    with attempts.attempt("ana@colegio.bo"):
        pass                                                # never runs: the account is locked
except RateLimited as locked:
    assert locked.retry_after <= timedelta(minutes=15)      # 429 with Retry-After, on every wire
assert attempts.locked_for("ana@colegio.bo") is not None    # what a screen shows
```

| `with attempts.attempt(key):` | |
|---|---|
| before the block | the account's turn: another attempt at it running — on any replica — is waited for; locked: `RateLimited`, the block never runs |
| the block raises `Unauthenticated` | one failure; the one that reaches `max_failures` locks the key |
| the block raises anything else | nothing counted: the project's database down is not a guess |
| the block ends well | the count starts over |

Attempts at one account take turns, so its failures are counted exactly: fifty guesses sent at
once are checked one after the other, and once `max_failures` of them failed the rest find the
account locked. The count is an atomic increment on the store every replica shares, keyed by the
username's digest, so an email never lands in Redis and a name nobody holds is counted and locked
like a real one. A store that does not answer lets the attempt through, logged: a Redis restart
never locks everyone out.

## A login use case

The account is the project's; the use case puts the pieces together — the password checked in the
account's turn, one answer whatever failed.

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
class SignIn(Feature):                                              # declares nothing: open
    def execute(self, dto: CommandSignIn) -> SignedIn:
        with attempts.attempt(dto.username):                        # locked: refused, no hash
            account = accounts.get(dto.username)
            good = hasher.verify(account.password_hash if account else None, dto.password)
            if not good or account is None or not account.active:
                raise Unauthenticated("wrong username or password")  # one answer, whatever failed
        if hasher.needs_rehash(account.password_hash):
            account.password_hash = hasher.hash(dto.password)
        return SignedIn(subject=f"user:{account.username}")


members_auth.on(members)
assert members(CommandSignIn(username="MFAQ0123", password="clave segura 123"), SignedIn).subject == "user:MFAQ0123"
```

What it answers — a token, a session — is the [token issuer](#issuing-tokens)'s: `return sessions.issue(...).answer()`.

## Issuing tokens

Once a person signed in, `TokenIssuer` hands out a short access token and an opaque refresh token,
and takes them back. Its `provider()` is the `JwtProvider` that accepts them — checking
revocations on every call.

```python
from sincpro_framework.auth import AccessControl, Assurance, Credentials, InMemoryRevocations
from sincpro_framework.auth.accounts import InMemoryRefreshTokens, SigningKeys, TokenIssuer

sessions = TokenIssuer(
    "https://colegio.bo",
    audience="colegio",
    keys=SigningKeys.hmac({"2026-09": "a token secret of thirty-two bytes!"}),   # a Secret
    refresh_tokens=InMemoryRefreshTokens(),   # KeyValueRefreshTokens() / DatabaseRefreshTokens
    revocations=InMemoryRevocations(),        # KeyValueRevocations()
)
tokens = sessions.issue(Identity.user("user:MFAQ0123", tenant="cauor"),
                        assurance=Assurance(methods=frozenset({"pwd"})))
answer = tokens.answer()                      # what the sign-in use case returns
assert set(answer) == {"access_token", "token_type", "expires_in", "refresh_token"}

session_auth = AccessControl[MembersPermission](providers=[sessions.provider()])
caller = Credentials(transport="http", headers={"authorization": f"Bearer {answer['access_token']}"})
assert session_auth.authenticate(caller).subject == "user:MFAQ0123"

renewed = sessions.refresh(answer["refresh_token"])          # the old refresh token is spent
sessions.revoke(renewed.access_token.get_secret_value())      # a logout: at once
try:
    session_auth.authenticate(Credentials(transport="http", headers={
        "authorization": f"Bearer {renewed.access_token.get_secret_value()}"}))
except Unauthenticated as refused:
    assert refused.reason == "session revoked"
```

| | |
|---|---|
| the access token | ten minutes; who, which session, when and how they authenticated, the scopes — no permissions: the provider's resolvers give them per call, so a role taken away holds at once (`embed_permissions=True` carries them with a version a change refuses, resolved by the issuer's `resolvers=` at sign-in and again at every refresh — with refresh tokens and no `resolvers=` it is refused at construction: a refresh never copies permissions a role taken away would outlive) |
| the refresh token | random, stored by digest, rotated on every use; used twice past `reuse_grace` (ten seconds — two tabs) it is a replay, and the whole session is revoked |
| a session | ends `idle_for` (14 days) after the last refresh and `at_most` (30 days) after it began |
| a logout | `revoke(token)` — an access or a refresh token — ends its session, its access tokens refused at once |
| the roles | the ones the identity carried at sign-in, kept with the session and carried by every refreshed token — never the ones a resolver added from a store: those are resolved again by whoever verifies the token, so a role taken away there is gone at once |
| "sign out everywhere" | `revoke_subject(subject)`: a password changed, an account disabled — every session of the subject ends; a new sign-in in that same second works |
| "signed in on…" | `sessions_of(subject)` — the live sessions, none signed out of |
| other services | `SigningKeys.private({...}, "ES256")` (`[jwt]`): they verify with `jwks()` and `TrustedIssuer(..., mapping=TokenIssuer.mapping)`, no secret shared |

## Links that work once, and a second factor

```python
from sincpro_framework.auth.accounts import OneTimeTokens, Totp

links = OneTimeTokens(InMemoryKeyValue())                  # the context's store in production
reset = links.issue("password-reset", "user:MFAQ0123", ttl=timedelta(minutes=30))
# … emailed as https://colegio.bo/restablecer?token=<reset> …
assert links.redeem("password-reset", reset).subject == "user:MFAQ0123"

totp = Totp(issuer="CAUOR", replay=InMemoryKeyValue())
secret = totp.new_secret()                                 # kept encrypted with the account
uri = totp.provisioning_uri(secret, "MFAQ0123")            # the QR code the app scans
assert totp.verify(secret, totp.now(secret), subject="user:MFAQ0123")
assert not totp.verify(secret, totp.now(secret), subject="user:MFAQ0123")   # once only
```

| `OneTimeTokens` | |
|---|---|
| bound to its purpose | stored by the token's digest under `purpose` — a reset link is never an invitation |
| once | taken atomically: two clicks at once redeem it once |
| the last one sent | `single_outstanding` (default): asking again voids the link before; `void(purpose, subject)` voids them all |
| replaces | an administrator handing a temporary password in an HTTP answer |

`Totp` is RFC 6238 on the standard library — its own test vectors pass — accepting a code within
one step either side; with `replay`, a step accepted for a subject is never accepted again, nor
an older one — `verify(secret, code, subject=...)`; without the subject a warning says the code
may be used twice. Six digits can be guessed: check each code inside `attempts.attempt(subject)`,
raising `Unauthenticated` when it is wrong, as a password. After a code,
the identity's `assurance` says `otp` — what a [step-up](server.md#step-up-how-strongly-how-recently--rfc-9470) asks for.

## HTTP Basic: service accounts

```python
import base64

from sincpro_framework.auth import Credentials
from sincpro_framework.auth.accounts import BasicProvider, HashedCredentials

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

## What it publishes

Given `publisher=`, each piece publishes what an audit and an incident response need — never a
token, a link or a password ([the audit trail](README.md#an-audit-trail-what-auth-publishes)):

| Piece | Events |
|---|---|
| `TokenIssuer` | `SignedIn` (session, client, methods, scopes), `SignedOut`, `SignedOutEverywhere`, `RefreshTokenReused` — a refresh token replayed past its grace: stolen or leaked, its session revoked |
| `LoginAttempts` | `AccountLocked` — the username counted, for how long |
| `OneTimeTokens` | `OneTimeTokenIssued`, `OneTimeTokenRedeemed` — the subject and the purpose |

## On the project's database

A service with no Redis keeps what auth stores in tables of its own database — declared in its
metadata and migrations like any other table, every store proven by the same contract as the
ones in memory and on Redis.

```python
from sqlalchemy import MetaData

from sincpro_framework.auth import KeyValueRevocations
from sincpro_framework.data_layer.orm import (
    Database,
    DatabaseKeyValue,
    DatabaseRefreshTokens,
    template_table,
)

auth_metadata = MetaData()
key_values = template_table.key_value_table("auth_key_value", auth_metadata)
refresh_table = template_table.refresh_token_table("refresh_token", auth_metadata)
database = Database("sqlite://")
auth_metadata.create_all(database.engine)          # a real project runs its migrations

on_database = TokenIssuer(
    "https://colegio.bo",
    audience="colegio",
    keys=SigningKeys.hmac({"2026-09": "a token secret of thirty-two bytes!"}),
    refresh_tokens=DatabaseRefreshTokens(database, refresh_table),
    revocations=KeyValueRevocations(DatabaseKeyValue(database, key_values)),
)
assert on_database.issue(Identity.user("user:MFAQ0123")).refresh_token is not None
```

| Table (`template_table.…`) | Store |
|---|---|
| `key_value_table` | `DatabaseKeyValue` — any `KeyValueStore` user: revocations, `LoginAttempts`, `OneTimeTokens`, `Totp`'s replay, leases |
| `api_key_table` | `DatabaseApiKeys` |
| `grant_assignment_table`, `entitlement_table` | `DatabaseGrants` |
| `refresh_token_table` | `DatabaseRefreshTokens` |
