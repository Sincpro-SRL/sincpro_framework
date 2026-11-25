"""Auth: who is calling and what they may do — the contract every door of the plugin builds on.

    class BillingPermission(Permission):
        ISSUE_INVOICE = "billing.invoice.issue"

    auth = AccessControl[BillingPermission](providers=[...])     # from auth.server, or yours
    auth.on(billing)

    @billing.feature(CommandIssueInvoice)
    @auth.requires(BillingPermission.ISSUE_INVOICE)              # undeclared: open
    class IssueInvoice(Feature): ...

Here: what a use case declares and asks (`AccessControl`, `requires`, `role`, `rule`,
`every_call`, `self.auth`), who calls (`Identity`, `Credentials`, `security`), the ports a project
implements (`AuthProvider`, `IdentityResolver`, `HttpClient`, `ClientAuthentication`), every auth
error, `IdentityMiddleware` for an ASGI app of the project's, and what the doors share —
revocations, OIDC discovery, JOSE keys, HMAC signature styles.

The doors, one per job — each a docs page under `docs/auth/`:

    sincpro_framework.auth.server     verify what arrives: JWT, introspection, API keys, service
                                      tokens, webhook signatures, mTLS, DPoP proofs, policy engines
    sincpro_framework.auth.client     present what goes out: token sources, client credentials,
                                      per tenant, exchange, refresh, device code, request signing
    sincpro_framework.auth.grants     roles and groups the Odoo 20 way: a catalog in code,
                                      `Grants` to resolve, assign and define them at runtime
    sincpro_framework.auth.accounts   accounts the service manages itself: passwords, lockout,
                                      HTTP Basic, one-time links, TOTP, the token issuer

Context: this package never imports a door, and no door loads PyJWT until a name of the `[jwt]`
extra is asked for (`PublicKey`, `PrivateKey` here). JSON-RPC, REST, FastAPI, gRPC, MCP, queues and
`remote_execution` authenticate by themselves when the bus is guarded.
"""

import importlib
from typing import TYPE_CHECKING, Any

from sincpro_framework.auth.access_control.adapters.added_requirements import (
    InMemoryAddedRequirements,
    KeyValueAddedRequirements,
)
from sincpro_framework.auth.access_control.domain.added_requirements import (
    AddedRequirement,
    AddedRequirementStore,
)
from sincpro_framework.auth.access_control.entrypoint.access_control import AccessControl
from sincpro_framework.auth.access_control.entrypoint.asgi import IdentityMiddleware
from sincpro_framework.auth.access_control.entrypoint.runtime_requirements import (
    RuntimeRequirements,
)
from sincpro_framework.auth.access_control.entrypoint.security import security
from sincpro_framework.auth.common.adapters.revocations import (
    InMemoryRevocations,
    KeyValueRevocations,
)
from sincpro_framework.auth.common.adapters.server_metadata import (
    OidcDiscovery,
    ServerMetadata,
)
from sincpro_framework.auth.common.adapters.shared_secrets import SharedSecrets
from sincpro_framework.auth.common.adapters.urllib_http import UrllibHttp
from sincpro_framework.auth.common.domain.administration import (
    AdministrationPolicy,
    BoundedAdministration,
    TrustedAdministration,
)
from sincpro_framework.auth.common.domain.events import (
    AccessRefused,
    AuthEvent,
    EventPublisher,
    RequirementAdded,
    RequirementRemoved,
)
from sincpro_framework.auth.common.domain.exceptions import (
    AuthError,
    ConcurrentChange,
    CredentialsRejected,
    EndpointUnavailable,
    GrantRejected,
    InvalidGrant,
    NoCredentialsFor,
    OAuthError,
    OAuthErrorCode,
    PasswordRejected,
    PermissionDenied,
    Unauthenticated,
    refusal_from,
)
from sincpro_framework.auth.common.domain.http import (
    ClientAuthentication,
    HttpAnswer,
    HttpClient,
    HttpRequest,
)
from sincpro_framework.auth.common.domain.identity import (
    Assurance,
    Credentials,
    Identity,
    IdentityKind,
)
from sincpro_framework.auth.common.domain.permissions import (
    OPEN,
    AccessCall,
    AccessDeclaration,
    AccessDescription,
    AnyOf,
    AssuranceRequirement,
    ByField,
    DeclarationKind,
    Permission,
    RoleRequirement,
    Rule,
    WhenDenied,
)
from sincpro_framework.auth.common.domain.provider import AuthProvider
from sincpro_framework.auth.common.domain.resolver import IdentityResolver
from sincpro_framework.auth.common.domain.revocations import (
    Revocations,
    RevocationState,
    WhenUnavailable,
    remembering_versions,
)
from sincpro_framework.auth.common.infrastructure.claim_mapping import ClaimMapping, ClaimTest
from sincpro_framework.auth.common.infrastructure.signatures import (
    HexDigest,
    SignatureStyle,
    TimestampedSignature,
)
from sincpro_framework.auth.common.infrastructure.trusted_issuer import TrustedIssuer

__all__ = [
    "OAuthError",
    "OAuthErrorCode",
    "TrustedIssuer",
    "SharedSecrets",
    "ClaimTest",
    "ClaimMapping",
    "OPEN",
    "AccessCall",
    "AccessControl",
    "AccessDeclaration",
    "AccessDescription",
    "AccessRefused",
    "AddedRequirement",
    "AddedRequirementStore",
    "AdministrationPolicy",
    "AnyOf",
    "Assurance",
    "AssuranceRequirement",
    "AuthError",
    "AuthEvent",
    "AuthProvider",
    "BoundedAdministration",
    "ByField",
    "ClientAuthentication",
    "ConcurrentChange",
    "Credentials",
    "CredentialsRejected",
    "DeclarationKind",
    "EndpointUnavailable",
    "EventPublisher",
    "GrantRejected",
    "HexDigest",
    "HttpAnswer",
    "HttpClient",
    "HttpRequest",
    "Identity",
    "IdentityKind",
    "IdentityMiddleware",
    "IdentityResolver",
    "InMemoryAddedRequirements",
    "InMemoryRevocations",
    "InvalidGrant",
    "KeyValueAddedRequirements",
    "KeyValueRevocations",
    "NoCredentialsFor",
    "OidcDiscovery",
    "PasswordRejected",
    "Permission",
    "PermissionDenied",
    "RequirementAdded",
    "RequirementRemoved",
    "Revocations",
    "RevocationState",
    "RoleRequirement",
    "Rule",
    "RuntimeRequirements",
    "ServerMetadata",
    "SignatureStyle",
    "TimestampedSignature",
    "TrustedAdministration",
    "Unauthenticated",
    "UrllibHttp",
    "WhenDenied",
    "WhenUnavailable",
    "refusal_from",
    "remembering_versions",
    "security",
]
"""Every name loaded with the package; the `[jwt]` extra's are in `_LAZY`, imported on first use."""

_LAZY = {
    "PrivateKey": "sincpro_framework.auth.common.adapters.jose_keys",
    "PublicKeys": "sincpro_framework.auth.common.adapters.public_keys",
    "PublicKey": "sincpro_framework.auth.common.adapters.jose_keys",
}
"""Names of the `[jwt]` extra, by the module that holds them — loaded on first use."""

if TYPE_CHECKING:
    from sincpro_framework.auth.common.adapters.jose_keys import PrivateKey, PublicKey
    from sincpro_framework.auth.common.adapters.public_keys import PublicKeys


def __getattr__(name: str) -> Any:
    """A name of the `[jwt]` extra, imported now — `ImportError` naming the extra when it is
    not installed."""
    if name in _LAZY:
        return getattr(importlib.import_module(_LAZY[name]), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
