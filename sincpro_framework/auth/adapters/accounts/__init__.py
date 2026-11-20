"""What a service that manages its own users is given: hashing passwords, locking guessed
accounts, verifying HTTP Basic credentials — and, beside them, one-time tokens, TOTP and the
token issuer."""

from sincpro_framework.auth.adapters.accounts.basic_provider import (
    BasicProvider,
    HashedCredentials,
)
from sincpro_framework.auth.adapters.accounts.login_attempts import LoginAttempts
from sincpro_framework.auth.adapters.accounts.passwords import PasswordHasher, ScryptCost

__all__ = [
    "BasicProvider",
    "HashedCredentials",
    "LoginAttempts",
    "PasswordHasher",
    "ScryptCost",
]
