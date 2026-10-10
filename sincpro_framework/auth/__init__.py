"""Auth: who is calling, and what they may do — permissions declared on use cases and hooks,
decided by providers, applied by a guard.

    class BillingPermission(Permission):
        ISSUE_INVOICE = "billing.invoice.issue"

    auth = AccessControl[BillingPermission](providers=[OdooProvider(...)])
    auth.on(billing)

    @billing.feature(CommandIssueInvoice)
    @auth.requires(BillingPermission.ISSUE_INVOICE)
    class IssueInvoice(Feature): ...

    with as_identity(auth.authenticate(credentials)):      # what an entrypoint does
        billing(CommandIssueInvoice(...))

`domain/` holds the vocabulary and the `AuthProvider` contract, `adapters/` the providers and
helpers the framework ships, `infrastructure/` who the execution acts as, `services/` the guard
that runs around a use case and before a hook, and `entrypoint/` `AccessControl` (the
declarations and the checks), what every wire hands auth (`transports`) and the middleware for an
ASGI app of the project's (`asgi`). JSON-RPC, gRPC, MCP and `remote_execution` authenticate by themselves when the bus is
guarded. See `docs/auth/README.md`.
"""

from sincpro_framework.auth.adapters.api_key_provider import (
    ApiKey,
    ApiKeyProvider,
    ApiKeyStore,
    InMemoryApiKeys,
)
from sincpro_framework.auth.adapters.role_permissions import RolePermissions
from sincpro_framework.auth.adapters.service_token_provider import ServiceTokenProvider
from sincpro_framework.auth.adapters.static_provider import StaticProvider
from sincpro_framework.auth.domain.exceptions import (
    AuthError,
    PermissionDenied,
    Unauthenticated,
)
from sincpro_framework.auth.domain.identity import Credentials, Identity, IdentityKind
from sincpro_framework.auth.domain.permissions import (
    AnyOf,
    Declaration,
    Permission,
    WhenDenied,
)
from sincpro_framework.auth.domain.provider import AuthProvider
from sincpro_framework.auth.entrypoint.access_control import (
    AccessControl,
    AccessDescription,
    access_control_of,
)
from sincpro_framework.auth.entrypoint.asgi import IdentityMiddleware
from sincpro_framework.auth.entrypoint.transports import (
    authenticated_as,
    credentials_from_asgi,
    credentials_from_headers,
)
from sincpro_framework.auth.infrastructure.security_context import (
    as_identity,
    as_system,
    current_identity,
)

__all__ = [
    "AccessControl",
    "AccessDescription",
    "ApiKey",
    "ApiKeyProvider",
    "ApiKeyStore",
    "AnyOf",
    "AuthError",
    "AuthProvider",
    "Credentials",
    "Declaration",
    "Identity",
    "IdentityKind",
    "IdentityMiddleware",
    "InMemoryApiKeys",
    "Permission",
    "PermissionDenied",
    "RolePermissions",
    "ServiceTokenProvider",
    "StaticProvider",
    "Unauthenticated",
    "WhenDenied",
    "access_control_of",
    "as_identity",
    "authenticated_as",
    "as_system",
    "credentials_from_asgi",
    "credentials_from_headers",
    "current_identity",
]
