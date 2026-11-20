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
"""

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
from sincpro_framework.auth.adapters.key_value_revocations import (
    InMemoryRevocations,
    KeyValueRevocations,
)
from sincpro_framework.auth.adapters.resolving import Resolving
from sincpro_framework.auth.adapters.role_permissions import RolePermissions
from sincpro_framework.auth.adapters.service_token_provider import ServiceTokenProvider
from sincpro_framework.auth.adapters.static_provider import StaticProvider
from sincpro_framework.auth.domain.exceptions import (
    AuthError,
    EndpointUnavailable,
    PermissionDenied,
    Unauthenticated,
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
from sincpro_framework.auth.domain.permissions import (
    AccessDeclaration,
    AnyOf,
    ByField,
    Permission,
    WhenDenied,
)
from sincpro_framework.auth.domain.provider import AuthProvider
from sincpro_framework.auth.domain.resolver import IdentityResolver
from sincpro_framework.auth.domain.revocations import Revocations, RevocationState
from sincpro_framework.auth.entrypoint.access_control import AccessControl, AccessDescription
from sincpro_framework.auth.entrypoint.asgi import IdentityMiddleware
from sincpro_framework.auth.entrypoint.security import security
from sincpro_framework.auth.infrastructure.urllib_http import UrllibHttp
from sincpro_framework.auth.services.server_metadata import OidcDiscovery, ServerMetadata

__all__ = [
    "AccessControl",
    "AccessDeclaration",
    "AccessDescription",
    "AnyOf",
    "ApiKey",
    "ApiKeyProvider",
    "ApiKeyStore",
    "Assurance",
    "AuthError",
    "AuthProvider",
    "ByField",
    "ClientAuthentication",
    "ClientSecretBasic",
    "ClientSecretJwt",
    "ClientSecretPost",
    "Credentials",
    "EndpointUnavailable",
    "HttpAnswer",
    "HttpClient",
    "HttpRequest",
    "Identity",
    "IdentityKind",
    "IdentityMiddleware",
    "IdentityResolver",
    "InMemoryApiKeys",
    "InMemoryRevocations",
    "KeyValueRevocations",
    "OidcDiscovery",
    "Permission",
    "PermissionDenied",
    "PublicClient",
    "Resolving",
    "RevocationState",
    "Revocations",
    "RolePermissions",
    "ServerMetadata",
    "ServiceTokenProvider",
    "StaticProvider",
    "Unauthenticated",
    "UrllibHttp",
    "WhenDenied",
    "security",
]
