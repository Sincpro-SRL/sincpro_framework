"""Auth: who is calling, and what they may do — permissions declared on use cases and hooks,
decided by providers, applied by a guard; and what this service presents when it calls another.

    class BillingPermission(Permission):
        ISSUE_INVOICE = "billing.invoice.issue"

    auth = AccessControl[BillingPermission](providers=[OdooProvider(...)])
    auth.on(billing)

    @billing.feature(CommandIssueInvoice)
    @auth.requires(BillingPermission.ISSUE_INVOICE)
    class IssueInvoice(Feature): ...

    with security.authenticated_as(billing, Credentials.from_asgi(scope)):   # an entrypoint
        billing(CommandIssueInvoice(...))

`domain/` holds the vocabulary and the ports (`AuthProvider`, `HttpClient`, `IdentityResolver`),
`adapters/` the providers and helpers the framework ships, `infrastructure/` who the execution
acts as, the JWS core and the standard library's HTTP client, `services/` the guard and what is
discovered of an authorization server, and `entrypoint/` `AccessControl` (the declarations and
the checks), `security` (who acts, and the door every wire hands auth what a caller presented)
and the middleware for an ASGI app of the project's (`asgi`). JSON-RPC, gRPC, MCP and
`remote_execution` authenticate by themselves when the bus is guarded. See `docs/auth/README.md`
and PRD_34 for the whole catalog.

The `[jwt]` extra's names — `Jwks`, `PublicKeys`, `PublicKey`, `PrivateKey`, `PrivateKeyJwt` — are
imported when
first asked for, so `import sincpro_framework.auth` never loads PyJWT.
"""

import importlib
from typing import TYPE_CHECKING, Any

from sincpro_framework.auth.adapters.accounts import (
    BasicProvider,
    HashedCredentials,
    LoginAttempts,
    PasswordHasher,
    ScryptCost,
)
from sincpro_framework.auth.adapters.api_key_provider import (
    ApiKey,
    ApiKeyProvider,
    ApiKeyStore,
    InMemoryApiKeys,
)
from sincpro_framework.auth.adapters.client_auth import (
    ClientSecretBasic,
    ClientSecretJwt,
    ClientSecretPost,
    PublicClient,
)
from sincpro_framework.auth.adapters.grants import (
    GrantResolver,
    InMemoryGrants,
    KeyValueGrants,
    RoleMapping,
    ScopeCeiling,
)
from sincpro_framework.auth.adapters.jwt import (
    ClaimMapping,
    ClaimTest,
    IntrospectionProvider,
    Issuer,
    JwtProvider,
    ProtectedResource,
    SharedSecrets,
)
from sincpro_framework.auth.adapters.key_value_revocations import (
    InMemoryRevocations,
    KeyValueRevocations,
)
from sincpro_framework.auth.adapters.outbound import (
    ApiKeyHeader,
    BasicCredentials,
    ClientCredentials,
    DeviceCode,
    DevicePrompt,
    InMemoryHeldGrants,
    KeyValueHeldGrants,
    RefreshingToken,
    RequestSigner,
    StaticToken,
    TokenExchange,
    TokenSources,
)
from sincpro_framework.auth.adapters.presenting import PresentingProvider
from sincpro_framework.auth.adapters.resolving import Resolving
from sincpro_framework.auth.adapters.role_permissions import RolePermissions
from sincpro_framework.auth.adapters.service_token_provider import ServiceTokenProvider
from sincpro_framework.auth.adapters.signature_provider import SignatureProvider
from sincpro_framework.auth.adapters.static_provider import StaticProvider
from sincpro_framework.auth.domain.accounts import CredentialVerifier
from sincpro_framework.auth.domain.exceptions import (
    AuthError,
    CredentialsRejected,
    EndpointUnavailable,
    GrantRejected,
    InvalidGrant,
    NoCredentialsFor,
    PasswordRejected,
    PermissionDenied,
    Unauthenticated,
)
from sincpro_framework.auth.domain.grants import (
    Assignment,
    CatalogDescription,
    Category,
    Entitlement,
    GrantCatalog,
    GrantSource,
    GrantStore,
    Held,
    Privilege,
    Role,
)
from sincpro_framework.auth.domain.http import (
    ClientAuthentication,
    HttpAnswer,
    HttpClient,
    HttpRequest,
)
from sincpro_framework.auth.domain.identity import (
    Assurance,
    Credentials,
    Identity,
    IdentityKind,
)
from sincpro_framework.auth.domain.outbound import (
    HeldGrant,
    HeldGrants,
    OutgoingRequest,
    Sealer,
    TokenSource,
)
from sincpro_framework.auth.domain.permissions import (
    AccessDeclaration,
    AnyOf,
    AssuranceRequirement,
    ByField,
    Permission,
    WhenDenied,
)
from sincpro_framework.auth.domain.provider import AuthProvider
from sincpro_framework.auth.domain.resolver import IdentityResolver
from sincpro_framework.auth.domain.revocations import Revocations, RevocationState
from sincpro_framework.auth.entrypoint.access_control import AccessControl, AccessDescription
from sincpro_framework.auth.entrypoint.asgi import IdentityMiddleware
from sincpro_framework.auth.entrypoint.outbound import (
    call_with_credentials,
    httpx_auth,
    requests_auth,
)
from sincpro_framework.auth.entrypoint.security import security
from sincpro_framework.auth.infrastructure.signatures import (
    HexDigest,
    SignatureStyle,
    TimestampedSignature,
)
from sincpro_framework.auth.infrastructure.urllib_http import UrllibHttp
from sincpro_framework.auth.services.grant_administration import GrantAdministration
from sincpro_framework.auth.services.server_metadata import OidcDiscovery, ServerMetadata
from sincpro_framework.auth.services.token_cache import TokenCache
from sincpro_framework.auth.services.token_endpoint import IssuedToken, TokenEndpoint

__all__ = [
    "AccessControl",
    "AccessDeclaration",
    "AccessDescription",
    "AnyOf",
    "ApiKey",
    "ApiKeyHeader",
    "ApiKeyProvider",
    "ApiKeyStore",
    "Assignment",
    "Assurance",
    "AssuranceRequirement",
    "AuthError",
    "AuthProvider",
    "BasicCredentials",
    "BasicProvider",
    "ByField",
    "CatalogDescription",
    "Category",
    "ClaimMapping",
    "ClaimTest",
    "ClientAuthentication",
    "ClientCredentials",
    "ClientSecretBasic",
    "ClientSecretJwt",
    "ClientSecretPost",
    "Credentials",
    "CredentialsRejected",
    "CredentialVerifier",
    "DeviceCode",
    "DevicePrompt",
    "EndpointUnavailable",
    "Entitlement",
    "GrantAdministration",
    "GrantCatalog",
    "GrantRejected",
    "GrantResolver",
    "GrantSource",
    "GrantStore",
    "HashedCredentials",
    "Held",
    "HeldGrant",
    "HeldGrants",
    "HexDigest",
    "HttpAnswer",
    "HttpClient",
    "HttpRequest",
    "Identity",
    "IdentityKind",
    "IdentityMiddleware",
    "IdentityResolver",
    "InMemoryApiKeys",
    "InMemoryGrants",
    "InMemoryHeldGrants",
    "InMemoryRevocations",
    "IntrospectionProvider",
    "InvalidGrant",
    "IssuedToken",
    "Issuer",
    "JwtProvider",
    "KeyValueGrants",
    "KeyValueHeldGrants",
    "KeyValueRevocations",
    "LoginAttempts",
    "NoCredentialsFor",
    "OidcDiscovery",
    "OutgoingRequest",
    "PasswordHasher",
    "PasswordRejected",
    "Permission",
    "PermissionDenied",
    "PresentingProvider",
    "Privilege",
    "ProtectedResource",
    "PublicClient",
    "RefreshingToken",
    "RequestSigner",
    "Resolving",
    "Revocations",
    "RevocationState",
    "Role",
    "RoleMapping",
    "RolePermissions",
    "ScopeCeiling",
    "ScryptCost",
    "Sealer",
    "ServerMetadata",
    "ServiceTokenProvider",
    "SharedSecrets",
    "SignatureProvider",
    "SignatureStyle",
    "StaticProvider",
    "StaticToken",
    "TimestampedSignature",
    "TokenCache",
    "TokenEndpoint",
    "TokenExchange",
    "TokenSource",
    "TokenSources",
    "Unauthenticated",
    "UrllibHttp",
    "WhenDenied",
    "call_with_credentials",
    "httpx_auth",
    "requests_auth",
    "security",
]

_LAZY = {
    "Jwks": "sincpro_framework.auth.adapters.jwt.jwks",
    "PublicKeys": "sincpro_framework.auth.adapters.jwt.jwks",
    "PublicKey": "sincpro_framework.auth.infrastructure.jose_keys",
    "PrivateKey": "sincpro_framework.auth.infrastructure.jose_keys",
    "PrivateKeyJwt": "sincpro_framework.auth.adapters.private_key_jwt",
}
"""Names of the `[jwt]` extra, by the module that holds them — loaded on first use."""

if TYPE_CHECKING:
    from sincpro_framework.auth.adapters.jwt.jwks import Jwks, PublicKeys
    from sincpro_framework.auth.adapters.private_key_jwt import PrivateKeyJwt
    from sincpro_framework.auth.infrastructure.jose_keys import PrivateKey, PublicKey


def __getattr__(name: str) -> Any:
    """A name of the `[jwt]` extra, imported now — `ImportError` naming the extra when it is
    not installed."""
    if name in _LAZY:
        return getattr(importlib.import_module(_LAZY[name]), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
