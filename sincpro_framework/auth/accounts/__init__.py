"""Accounts the service manages itself — never a user table nor a login screen: the project stores
its accounts as entities of its own, and these pieces hash, lock, link and issue.

    from sincpro_framework.auth.accounts import LoginAttempts, PasswordHasher, TokenIssuer

| Job | Pieces |
|---|---|
| passwords | `PasswordHasher` (scrypt, argon2id with `[argon2]`, legacy migration), `ScryptCost` |
| guessing | `LoginAttempts` — a username locked after too many failures |
| HTTP Basic | `BasicProvider` over a `CredentialVerifier` — the project's accounts, or `HashedCredentials` |
| a link sent by email | `OneTimeTokens`, `OneTimeTokenRecord` |
| a second factor | `Totp` (RFC 6238), `TotpAlgorithm` |
| tokens of its own | `TokenIssuer`, `IssuedTokens`, `SigningKeys`; refresh tokens in `RefreshTokens` (`KeyValueRefreshTokens`, `InMemoryRefreshTokens`; `DatabaseRefreshTokens` in `data_layer.orm`); how long a session lives, `SessionLifetime`; what it keeps on the server, `SessionAttributes` (`KeyValueSessionAttributes`, `InMemorySessionAttributes`) |

`auth.server.JwtProvider.for_token_issuer(issuer)` verifies what a `TokenIssuer` issued, by its `trusted_issuer()`.
"""

from sincpro_framework.auth.accounts.adapters.password_hasher import (
    HashScheme,
    PasswordHasher,
    ScryptCost,
)
from sincpro_framework.auth.accounts.adapters.refresh_tokens import (
    InMemoryRefreshTokens,
    KeyValueRefreshTokens,
)
from sincpro_framework.auth.accounts.adapters.session_attributes import (
    InMemorySessionAttributes,
    KeyValueSessionAttributes,
)
from sincpro_framework.auth.accounts.adapters.signing_keys import SigningKeys
from sincpro_framework.auth.accounts.domain.credential_verifier import CredentialVerifier
from sincpro_framework.auth.accounts.domain.events import (
    AccountLocked,
    OneTimeTokenIssued,
    OneTimeTokenRedeemed,
    RefreshTokenReused,
    SignedIn,
    SignedOut,
    SignedOutEverywhere,
)
from sincpro_framework.auth.accounts.domain.refresh_tokens import RefreshRecord, RefreshTokens
from sincpro_framework.auth.accounts.domain.sessions import SessionAttributes, SessionLifetime
from sincpro_framework.auth.accounts.services.http_basic import (
    BasicProvider,
    HashedCredentials,
)
from sincpro_framework.auth.accounts.services.login_attempts import LoginAttempts
from sincpro_framework.auth.accounts.services.one_time_tokens import (
    OneTimeTokenRecord,
    OneTimeTokens,
)
from sincpro_framework.auth.accounts.services.token_issuer import (
    IssuedTokens,
    KeptClaims,
    TokenIssuer,
)
from sincpro_framework.auth.accounts.services.totp import Totp, TotpAlgorithm

__all__ = [
    "AccountLocked",
    "BasicProvider",
    "CredentialVerifier",
    "HashScheme",
    "HashedCredentials",
    "InMemoryRefreshTokens",
    "InMemorySessionAttributes",
    "IssuedTokens",
    "KeptClaims",
    "KeyValueRefreshTokens",
    "KeyValueSessionAttributes",
    "LoginAttempts",
    "OneTimeTokenIssued",
    "OneTimeTokenRecord",
    "OneTimeTokenRedeemed",
    "OneTimeTokens",
    "PasswordHasher",
    "RefreshRecord",
    "RefreshTokenReused",
    "RefreshTokens",
    "ScryptCost",
    "SessionAttributes",
    "SessionLifetime",
    "SignedIn",
    "SignedOut",
    "SignedOutEverywhere",
    "SigningKeys",
    "TokenIssuer",
    "Totp",
    "TotpAlgorithm",
]
