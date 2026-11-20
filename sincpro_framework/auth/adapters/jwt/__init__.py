"""JWT bearer tokens: `JwtProvider`, the `Issuer`s it trusts, how their claims read
(`ClaimMapping`), where their keys come from (`SharedSecrets` here; `Jwks` and `PublicKeys`,
the `[jwt]` extra's, in `jwks`) and what the service says about itself (`ProtectedResource`).
"""

from sincpro_framework.auth.adapters.jwt.introspection import IntrospectionProvider
from sincpro_framework.auth.adapters.jwt.issuer import IssuedTokens, TokenIssuer
from sincpro_framework.auth.adapters.jwt.keys import SharedSecrets
from sincpro_framework.auth.adapters.jwt.mapping import ClaimMapping, ClaimTest
from sincpro_framework.auth.adapters.jwt.provider import Issuer, JwtProvider
from sincpro_framework.auth.adapters.jwt.resource import ProtectedResource
from sincpro_framework.auth.adapters.jwt.signing import SigningKeys

__all__ = [
    "ClaimMapping",
    "ClaimTest",
    "IntrospectionProvider",
    "IssuedTokens",
    "Issuer",
    "JwtProvider",
    "ProtectedResource",
    "SharedSecrets",
    "SigningKeys",
    "TokenIssuer",
]
