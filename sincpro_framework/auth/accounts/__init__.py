"""Accounts the service manages itself — never a user table nor a login screen: the project keeps
its accounts as entities of its own, and these pieces hash, lock, link and issue.

    from sincpro_framework.auth.accounts import LoginAttempts, PasswordHasher, TokenIssuer

| Job | Pieces |
|---|---|
| passwords | `PasswordHasher` (scrypt, argon2id with `[argon2]`, legacy migration), `ScryptCost` |
| guessing | `LoginAttempts` — a username locked after too many failures |
| HTTP Basic | `BasicProvider` over a `CredentialVerifier` — the project's accounts, or `HashedCredentials` |
| a link sent by email | `OneTimeTokens`, `OneTimeGrant` |
| a second factor | `Totp` (RFC 6238) |
| tokens of its own | `TokenIssuer`, `IssuedTokens`, `SigningKeys`; refresh tokens in `RefreshTokens` (`InMemoryRefreshTokens`, `KeyValueRefreshTokens`; `DatabaseRefreshTokens` in `data_layer.orm`) |

`TokenIssuer.provider()` is the `auth.server.JwtProvider` that verifies what it issued.
"""

from sincpro_framework.auth.accounts.adapters.issuing.refresh_tokens import (
    InMemoryRefreshTokens,
    KeyValueRefreshTokens,
)
from sincpro_framework.auth.accounts.adapters.issuing.signing_keys import SigningKeys
from sincpro_framework.auth.accounts.adapters.issuing.token_issuer import (
    IssuedTokens,
    TokenIssuer,
)
from sincpro_framework.auth.accounts.adapters.one_time_tokens import (
    OneTimeGrant,
    OneTimeTokens,
)
from sincpro_framework.auth.accounts.adapters.passwords.hasher import (
    PasswordHasher,
    ScryptCost,
)
from sincpro_framework.auth.accounts.adapters.passwords.http_basic import (
    BasicProvider,
    HashedCredentials,
)
from sincpro_framework.auth.accounts.adapters.passwords.login_attempts import LoginAttempts
from sincpro_framework.auth.accounts.adapters.totp import Totp
from sincpro_framework.auth.accounts.domain.credential_verifier import CredentialVerifier
from sincpro_framework.auth.accounts.domain.refresh_tokens import RefreshRecord, RefreshTokens

__all__ = [
    "BasicProvider",
    "CredentialVerifier",
    "HashedCredentials",
    "InMemoryRefreshTokens",
    "IssuedTokens",
    "KeyValueRefreshTokens",
    "LoginAttempts",
    "OneTimeGrant",
    "OneTimeTokens",
    "PasswordHasher",
    "RefreshRecord",
    "RefreshTokens",
    "ScryptCost",
    "SigningKeys",
    "TokenIssuer",
    "Totp",
]
