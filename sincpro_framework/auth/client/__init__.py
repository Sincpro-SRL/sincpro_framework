"""What goes out — the credentials this service presents when it calls another, renewed before
they lapse and shared by every replica.

    from sincpro_framework.auth.client import ClientCredentials, OutgoingRequest, call_with_credentials

    bank = ClientCredentials(client_id="billing", client_secret=secret, issuer="https://auth.bank.bo")
    call_with_credentials(bank, OutgoingRequest(url="https://api.bank.bo/qr"), send)

| Presents | Source |
|---|---|
| a fixed token, an API key, HTTP Basic | `StaticToken`, `ApiKeyHeader`, `BasicCredentials` |
| its own client credentials (RFC 6749 §4.4, RFC 8707) | `ClientCredentials`, cached in a `TokenCache` |
| a credential of each tenant, never another's | `TenantTokenSources` |
| the caller's token, exchanged for the next service (RFC 8693) | `TokenExchange`, `OutboundCredentialsProvider` |
| a user's grant stored and refreshed | `RefreshTokenSource`, `OAuthGrantStore` (`InMemoryOAuthGrants`, `KeyValueOAuthGrants`) |
| a device's authorization (RFC 8628) | `DeviceAuthorization`, `DeviceAuthorizationPrompt` |
| a signed request, a DPoP proof | `RequestSigner`, `DpopProver` |
| a project's own grant, cached | a subclass of `CachedTokenSource` |

Every source is a `TokenSource`. How this service authenticates to a token endpoint —
`ClientSecretBasic`, `ClientSecretPost`, `ClientSecretJwt`, `PrivateKeyJwt`, `PublicClient` — is
the root's `ClientAuthentication` port; an `auth.server.IntrospectionProvider` takes them too, the
server asking the authorization server as its client. `call_with_credentials`
retries once on a 401 with a fresh credential; `requests_auth` and `httpx_auth` are the same for
those libraries.

Context: `PrivateKeyJwt`, `DpopProver` and `AesGcmSealer` need the `[jwt]` extra and are imported
when first asked for.
"""

import importlib
from typing import TYPE_CHECKING, Any

from sincpro_framework.auth.client.adapters.cached_token_source import CachedTokenSource
from sincpro_framework.auth.client.adapters.client_authentication import (
    ClientSecretBasic,
    ClientSecretJwt,
    ClientSecretPost,
    PublicClient,
)
from sincpro_framework.auth.client.adapters.client_credentials import ClientCredentials
from sincpro_framework.auth.client.adapters.device_authorization import (
    DeviceAuthorization,
    DeviceAuthorizationPrompt,
)
from sincpro_framework.auth.client.adapters.oauth_grant_store import (
    InMemoryOAuthGrants,
    KeyValueOAuthGrants,
)
from sincpro_framework.auth.client.adapters.outbound_provider import (
    OutboundCredentialsProvider,
)
from sincpro_framework.auth.client.adapters.request_signer import RequestSigner
from sincpro_framework.auth.client.adapters.static_credentials import (
    ApiKeyHeader,
    BasicCredentials,
    StaticToken,
)
from sincpro_framework.auth.client.adapters.tenant_token_sources import TenantTokenSources
from sincpro_framework.auth.client.adapters.token_cache import TokenCache
from sincpro_framework.auth.client.adapters.token_endpoint import TokenEndpoint
from sincpro_framework.auth.client.adapters.token_exchange import TokenExchange
from sincpro_framework.auth.client.domain.outbound import (
    OAuthGrant,
    OAuthGrantStore,
    OutgoingRequest,
    Sealer,
    TokenSource,
)
from sincpro_framework.auth.client.domain.token_response import (
    TokenResponse,
    TokenType,
    TokenTypeIdentifier,
)
from sincpro_framework.auth.client.entrypoint.authenticated_call import (
    call_with_credentials,
    httpx_auth,
    requests_auth,
)
from sincpro_framework.auth.client.services.refresh_token_source import RefreshTokenSource

__all__ = [
    "ApiKeyHeader",
    "BasicCredentials",
    "CachedTokenSource",
    "ClientCredentials",
    "ClientSecretBasic",
    "ClientSecretJwt",
    "ClientSecretPost",
    "DeviceAuthorization",
    "DeviceAuthorizationPrompt",
    "InMemoryOAuthGrants",
    "KeyValueOAuthGrants",
    "OAuthGrant",
    "OAuthGrantStore",
    "OutboundCredentialsProvider",
    "OutgoingRequest",
    "PublicClient",
    "RefreshTokenSource",
    "RequestSigner",
    "Sealer",
    "StaticToken",
    "TenantTokenSources",
    "TokenCache",
    "TokenEndpoint",
    "TokenExchange",
    "TokenResponse",
    "TokenSource",
    "TokenType",
    "TokenTypeIdentifier",
    "call_with_credentials",
    "httpx_auth",
    "requests_auth",
]
"""Every name loaded with the package; the `[jwt]` extra's are in `_LAZY`, imported on first use."""

_LAZY = {
    "AesGcmSealer": "sincpro_framework.auth.client.adapters.aes_gcm_sealer",
    "DpopProver": "sincpro_framework.auth.client.adapters.dpop_prover",
    "PrivateKeyJwt": "sincpro_framework.auth.client.adapters.private_key_jwt",
}
"""Names of the `[jwt]` extra, by the module that holds them — loaded on first use."""

if TYPE_CHECKING:
    from sincpro_framework.auth.client.adapters.aes_gcm_sealer import AesGcmSealer
    from sincpro_framework.auth.client.adapters.dpop_prover import DpopProver
    from sincpro_framework.auth.client.adapters.private_key_jwt import PrivateKeyJwt


def __getattr__(name: str) -> Any:
    """A name of the `[jwt]` extra, imported now — `ImportError` naming the extra when it is
    not installed."""
    if name in _LAZY:
        return getattr(importlib.import_module(_LAZY[name]), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
