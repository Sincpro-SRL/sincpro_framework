"""What arrives, verified — the providers an `AccessControl` asks who is calling.

    from sincpro_framework.auth.server import ClaimMapping, JwtProvider

    auth = AccessControl[P](providers=[
        JwtProvider.from_issuer(KEYCLOAK, audience="billing", mapping=ClaimMapping.keycloak()),
        ApiKeyProvider(keys),                     # integrations
        ServiceTokenProvider("billing", secrets), # this platform's own services
    ])

| Arrives | Provider |
|---|---|
| a bearer JWT of Keycloak, Entra, Google — several issuers | `JwtProvider`, `TrustedIssuer`, `ClaimMapping`, keys by `Jwks` / `PublicKeys` / `SharedSecrets` |
| an opaque token (RFC 7662) | `IntrospectionProvider` |
| a token bound to a key (RFC 9449) or a certificate (RFC 8705) | `DpopProofs`, `CertificateProvider` |
| an API key | `ApiKeyProvider`, `ApiKey`, `ApiKeyStore`, `InMemoryApiKeys` |
| a call from another service of this platform | `ServiceTokenProvider` |
| a webhook signed with HMAC | `SignatureProvider` |
| a static token (a test, a script) | `StaticProvider` |
| a decision taken elsewhere (AuthZEN, OPA, OpenFGA) | `DecidedBy`, `PolicyEngine` and its engines |

`Resolving(provider, *resolvers)` gives a provider of the project's the resolvers the shipped ones
take (`resolvers=`): roles to permissions (`auth.grants`), a tenant's own. `ProtectedResource`
says what this service is, for a client to discover (RFC 9728). HTTP Basic against accounts of the
service's own is `auth.accounts.BasicProvider`.

Context: `Jwks`, `PublicKeys`, `DpopProofs` and `CertificateProvider` need the `[jwt]` extra and
are imported when first asked for.
"""

import importlib
from typing import TYPE_CHECKING, Any

from sincpro_framework.auth.server.adapters.api_keys import (
    ApiKey,
    ApiKeyProvider,
    ApiKeyStore,
    InMemoryApiKeys,
    api_key_digest,
)
from sincpro_framework.auth.server.adapters.bearer_tokens.claim_mapping import (
    ClaimMapping,
    ClaimTest,
)
from sincpro_framework.auth.server.adapters.bearer_tokens.introspection import (
    IntrospectionProvider,
)
from sincpro_framework.auth.server.adapters.bearer_tokens.jwt_provider import (
    JwtProvider,
    TrustedIssuer,
)
from sincpro_framework.auth.server.adapters.bearer_tokens.protected_resource import (
    ProtectedResource,
)
from sincpro_framework.auth.server.adapters.bearer_tokens.shared_secrets import SharedSecrets
from sincpro_framework.auth.server.adapters.policy.decided_by import DecidedBy
from sincpro_framework.auth.server.adapters.policy.engines import (
    AuthZenEngine,
    OpaEngine,
    OpenFgaEngine,
)
from sincpro_framework.auth.server.adapters.resolving import Resolving
from sincpro_framework.auth.server.adapters.service_tokens import ServiceTokenProvider
from sincpro_framework.auth.server.adapters.static_tokens import StaticProvider
from sincpro_framework.auth.server.adapters.webhook_signatures import SignatureProvider
from sincpro_framework.auth.server.domain.policy import PolicyEngine

__all__ = [
    "ApiKey",
    "ApiKeyProvider",
    "ApiKeyStore",
    "AuthZenEngine",
    "ClaimMapping",
    "ClaimTest",
    "DecidedBy",
    "InMemoryApiKeys",
    "IntrospectionProvider",
    "JwtProvider",
    "OpaEngine",
    "OpenFgaEngine",
    "PolicyEngine",
    "ProtectedResource",
    "Resolving",
    "ServiceTokenProvider",
    "SharedSecrets",
    "SignatureProvider",
    "StaticProvider",
    "TrustedIssuer",
    "api_key_digest",
]
"""Every name loaded with the package; the `[jwt]` extra's are in `_LAZY`, imported on first use."""

_LAZY = {
    "CertificateProvider": "sincpro_framework.auth.server.adapters.certificates",
    "DpopProofs": "sincpro_framework.auth.server.adapters.bearer_tokens.dpop_proofs",
    "Jwks": "sincpro_framework.auth.server.adapters.bearer_tokens.jwks",
    "PublicKeys": "sincpro_framework.auth.server.adapters.bearer_tokens.jwks",
}
"""Names of the `[jwt]` extra, by the module that holds them — loaded on first use."""

if TYPE_CHECKING:
    from sincpro_framework.auth.server.adapters.bearer_tokens.dpop_proofs import DpopProofs
    from sincpro_framework.auth.server.adapters.bearer_tokens.jwks import Jwks, PublicKeys
    from sincpro_framework.auth.server.adapters.certificates import CertificateProvider


def __getattr__(name: str) -> Any:
    """A name of the `[jwt]` extra, imported now — `ImportError` naming the extra when it is
    not installed."""
    if name in _LAZY:
        return getattr(importlib.import_module(_LAZY[name]), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
