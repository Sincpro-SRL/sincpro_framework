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
helpers the framework ships, `security_context` who the execution acts as, `guard` what runs
around a use case and before a hook, `access_control` the declarations and the checks. See
`docs/auth/README.md`.
"""

from sincpro_framework.auth.access_control import AccessControl, AccessDescription
from sincpro_framework.auth.adapters import RolePermissions, StaticProvider
from sincpro_framework.auth.domain import (
    AnyOf,
    AuthError,
    AuthProvider,
    Credentials,
    Identity,
    IdentityKind,
    Permission,
    PermissionDenied,
    Unauthenticated,
    WhenDenied,
)
from sincpro_framework.auth.guard import Declaration
from sincpro_framework.auth.security_context import as_identity, as_system, current_identity

__all__ = [
    "AccessControl",
    "AccessDescription",
    "AnyOf",
    "AuthError",
    "AuthProvider",
    "Credentials",
    "Declaration",
    "Identity",
    "IdentityKind",
    "Permission",
    "PermissionDenied",
    "RolePermissions",
    "StaticProvider",
    "Unauthenticated",
    "WhenDenied",
    "as_identity",
    "as_system",
    "current_identity",
]
