"""The vocabulary: who is calling and what they presented, what may be required, what a refusal
raises, and the contract a provider implements."""

from sincpro_framework.auth.domain.exceptions import (
    AuthError,
    PermissionDenied,
    Unauthenticated,
)
from sincpro_framework.auth.domain.identity import (
    ANONYMOUS,
    Credentials,
    Identity,
    IdentityKind,
)
from sincpro_framework.auth.domain.permissions import (
    AnyOf,
    Permission,
    Requirement,
    WhenDenied,
)
from sincpro_framework.auth.domain.provider import AuthProvider

__all__ = [
    "ANONYMOUS",
    "AnyOf",
    "AuthError",
    "AuthProvider",
    "Credentials",
    "Identity",
    "IdentityKind",
    "Permission",
    "PermissionDenied",
    "Requirement",
    "Unauthenticated",
    "WhenDenied",
]
