"""What arrives, verified — the providers an `AccessControl` asks who is calling.

    from sincpro_framework.auth import ClaimMapping
    from sincpro_framework.auth.server import JwtProvider

    auth = AccessControl[P](providers=[
        JwtProvider.from_issuer(KEYCLOAK, audience="billing", mapping=ClaimMapping.keycloak()),
        ApiKeyProvider(keys),                     # integrations
        ServiceTokenProvider("billing", secrets), # this platform's own services
    ])

| Arrives | Provider |
|---|---|
| a bearer JWT of Keycloak, Entra, Google — several issuers | `JwtProvider` over a `TrustedIssuer`, `ClaimMapping` and keys by `Jwks` (here) or `PublicKeys` / `SharedSecrets` (`sincpro_framework.auth`) |
| an opaque token (RFC 7662) | `IntrospectionProvider` |
| a token bound to a key (RFC 9449) or a certificate (RFC 8705) | `DpopProofs`, `CertificateProvider` |
| an API key | `ApiKeyProvider`, `ApiKey`, `ApiKeyStore`, `InMemoryApiKeys` |
| a call from another service of this platform | `ServiceTokenProvider` |
| a webhook signed with HMAC | `SignatureProvider` |
| a static token (a test, a script) | `StaticProvider` |
| a decision taken elsewhere (AuthZEN, OPA, OpenFGA) | `PolicyEngineProvider`, `PolicyEngine` and its engines |

`ResolvingProvider(provider, *resolvers)` gives a provider of the project's the resolvers the shipped ones
take (`resolvers=`): roles to permissions (`auth.grants`), a tenant's own. `ProtectedResource`
says what this service is, for a client to discover (RFC 9728). HTTP Basic against accounts of the
service's own is `auth.accounts.BasicProvider`.

Context: `Jwks`, `DpopProofs` and `CertificateProvider` need the `[jwt]` extra and
are imported when first asked for.
"""

import importlib
from typing import TYPE_CHECKING, Any

from sincpro_framework.auth.server.adapters.authorization_server.oauth_clients import (
    InMemoryOAuthClients,
    KeyValueOAuthClients,
)
from sincpro_framework.auth.server.adapters.resource_server.api_keys import (
    ApiKey,
    ApiKeyProvider,
    ApiKeyStore,
    InMemoryApiKeys,
    api_key_digest,
)
from sincpro_framework.auth.server.adapters.resource_server.bearer_tokens.introspection import (
    IntrospectionProvider,
)
from sincpro_framework.auth.server.adapters.resource_server.bearer_tokens.jwt_provider import (
    JwtProvider,
)
from sincpro_framework.auth.server.adapters.resource_server.policy.engines import (
    AuthZenEngine,
    OpaEngine,
    OpenFgaEngine,
)
from sincpro_framework.auth.server.adapters.resource_server.service_tokens import (
    ServiceTokenProvider,
)
from sincpro_framework.auth.server.adapters.resource_server.static_tokens import (
    StaticProvider,
)
from sincpro_framework.auth.server.adapters.resource_server.webhook_signatures import (
    SignatureProvider,
)
from sincpro_framework.auth.server.domain.authorization_server.authorization import (
    AuthorizationCode,
    AuthorizationRequest,
    CodeChallengeMethod,
    PendingAuthorization,
    PkcePolicy,
)
from sincpro_framework.auth.server.domain.authorization_server.clients import (
    ClientAuthMethod,
    ClientRegistry,
    GrantType,
    OAuthClient,
)
from sincpro_framework.auth.server.domain.authorization_server.endpoints import (
    AuthorizationServerEndpoint,
)
from sincpro_framework.auth.server.domain.resource_server.policy import PolicyEngine
from sincpro_framework.auth.server.domain.resource_server.protected_resource import (
    ProtectedResource,
)
from sincpro_framework.auth.server.entrypoint.authorization_server import AuthorizationServer
from sincpro_framework.auth.server.services.authorization_server.authorization_code import (
    AuthorizationCodeGrant,
)
from sincpro_framework.auth.server.services.authorization_server.core import (
    AuthorizationServerCore,
)
from sincpro_framework.auth.server.services.authorization_server.metadata import (
    AuthorizationServerMetadata,
)
from sincpro_framework.auth.server.services.authorization_server.refresh import (
    RefreshTokenGrant,
)
from sincpro_framework.auth.server.services.authorization_server.registration import (
    ClientRegistration,
    RegisteredClient,
)
from sincpro_framework.auth.server.services.authorization_server.revocation import (
    TokenRevocation,
)
from sincpro_framework.auth.server.services.resource_server.policy_engine_provider import (
    PolicyEngineProvider,
)
from sincpro_framework.auth.server.services.resource_server.resolving_provider import (
    ResolvingProvider,
)

__all__ = [
    "api_key_digest",
    "ApiKey",
    "ApiKeyProvider",
    "ApiKeyStore",
    "AuthorizationCode",
    "AuthorizationCodeGrant",
    "AuthorizationRequest",
    "AuthorizationServer",
    "AuthorizationServerCore",
    "AuthorizationServerEndpoint",
    "AuthZenEngine",
    "ClientAuthMethod",
    "ClientRegistration",
    "ClientRegistry",
    "CodeChallengeMethod",
    "PolicyEngineProvider",
    "GrantType",
    "InMemoryApiKeys",
    "InMemoryOAuthClients",
    "IntrospectionProvider",
    "JwtProvider",
    "KeyValueOAuthClients",
    "OAuthClient",
    "OpaEngine",
    "OpenFgaEngine",
    "PendingAuthorization",
    "PkcePolicy",
    "PolicyEngine",
    "ProtectedResource",
    "RefreshTokenGrant",
    "RegisteredClient",
    "ResolvingProvider",
    "AuthorizationServerMetadata",
    "ServiceTokenProvider",
    "SignatureProvider",
    "StaticProvider",
    "TokenRevocation",
]
"""Every name loaded with the package; the `[jwt]` extra's are in `_LAZY`, imported on first use."""

_LAZY = {
    "CertificateProvider": "sincpro_framework.auth.server.adapters.resource_server.certificates",
    "DpopProofs": "sincpro_framework.auth.server.adapters.resource_server.bearer_tokens.dpop_proofs",
    "Jwks": "sincpro_framework.auth.server.adapters.resource_server.bearer_tokens.jwks",
}
"""Names of the `[jwt]` extra, by the module that holds them — loaded on first use."""

if TYPE_CHECKING:
    from sincpro_framework.auth.server.adapters.resource_server.bearer_tokens.dpop_proofs import (
        DpopProofs,
    )
    from sincpro_framework.auth.server.adapters.resource_server.bearer_tokens.jwks import Jwks
    from sincpro_framework.auth.server.adapters.resource_server.certificates import (
        CertificateProvider,
    )


def __getattr__(name: str) -> Any:
    """A name of the `[jwt]` extra, imported now — `ImportError` naming the extra when it is
    not installed."""
    if name in _LAZY:
        return getattr(importlib.import_module(_LAZY[name]), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
