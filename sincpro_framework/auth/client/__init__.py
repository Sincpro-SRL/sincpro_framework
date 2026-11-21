"""What goes out — the credentials this service presents when it calls another, renewed before
they lapse and shared by every replica.

    from sincpro_framework.auth.client import ClientCredentials, OutgoingRequest, call_with_credentials

    bank = ClientCredentials(client_id="billing", client_secret=secret, issuer="https://auth.bank.bo")
    call_with_credentials(bank, OutgoingRequest(url="https://api.bank.bo/qr"), send)

| Presents | Source |
|---|---|
| a fixed token, an API key, HTTP Basic | `StaticToken`, `ApiKeyHeader`, `BasicCredentials` |
| its own client credentials (RFC 6749 §4.4, RFC 8707) | `ClientCredentials`, cached in a `TokenCache` |
| a credential of each tenant, never another's | `TokenSources` |
| the caller's token, exchanged for the next service (RFC 8693) | `TokenExchange`, `PresentingProvider` |
| a user's grant held and refreshed | `RefreshingToken`, `HeldGrants` (`InMemoryHeldGrants`, `KeyValueHeldGrants`) |
| a device's authorization (RFC 8628) | `DeviceCode`, `DevicePrompt` |
| a signed request, a DPoP proof | `RequestSigner`, `DpopProver` |

Every source is a `TokenSource`. How the client authenticates to a token endpoint is a
`ClientAuthentication` of the root: `ClientSecretBasic`, `ClientSecretPost`, `ClientSecretJwt`,
`PrivateKeyJwt`, `PublicClient` — an introspecting server uses them too. `call_with_credentials`
retries once on a 401 with a fresh credential; `requests_auth` and `httpx_auth` are the same for
those libraries.

Context: `PrivateKeyJwt`, `DpopProver` and `AesGcmSealer` need the `[jwt]` extra and are imported
when first asked for.
"""

import importlib
from typing import TYPE_CHECKING, Any

from sincpro_framework.auth.client.adapters.client_authentication import (
    ClientSecretBasic,
    ClientSecretJwt,
    ClientSecretPost,
    PublicClient,
)
from sincpro_framework.auth.client.adapters.client_credentials import ClientCredentials
from sincpro_framework.auth.client.adapters.device_code import DeviceCode, DevicePrompt
from sincpro_framework.auth.client.adapters.exchange import TokenExchange
from sincpro_framework.auth.client.adapters.held_grants import (
    InMemoryHeldGrants,
    KeyValueHeldGrants,
)
from sincpro_framework.auth.client.adapters.per_tenant import TokenSources
from sincpro_framework.auth.client.adapters.presenting import PresentingProvider
from sincpro_framework.auth.client.adapters.refreshing import RefreshingToken
from sincpro_framework.auth.client.adapters.request_signer import RequestSigner
from sincpro_framework.auth.client.adapters.static_credentials import (
    ApiKeyHeader,
    BasicCredentials,
    StaticToken,
)
from sincpro_framework.auth.client.domain.outbound import (
    HeldGrant,
    HeldGrants,
    OutgoingRequest,
    Sealer,
    TokenSource,
)
from sincpro_framework.auth.client.entrypoint.calling import (
    call_with_credentials,
    httpx_auth,
    requests_auth,
)
from sincpro_framework.auth.client.infrastructure.token_cache import TokenCache
from sincpro_framework.auth.client.infrastructure.token_endpoint import (
    IssuedToken,
    TokenEndpoint,
)

__all__ = [
    "AesGcmSealer",
    "ApiKeyHeader",
    "BasicCredentials",
    "ClientCredentials",
    "ClientSecretBasic",
    "ClientSecretJwt",
    "ClientSecretPost",
    "DeviceCode",
    "DevicePrompt",
    "DpopProver",
    "HeldGrant",
    "HeldGrants",
    "InMemoryHeldGrants",
    "IssuedToken",
    "KeyValueHeldGrants",
    "OutgoingRequest",
    "PresentingProvider",
    "PrivateKeyJwt",
    "PublicClient",
    "RefreshingToken",
    "RequestSigner",
    "Sealer",
    "StaticToken",
    "TokenCache",
    "TokenEndpoint",
    "TokenExchange",
    "TokenSource",
    "TokenSources",
    "call_with_credentials",
    "httpx_auth",
    "requests_auth",
]

_LAZY = {
    "AesGcmSealer": "sincpro_framework.auth.client.infrastructure.sealing",
    "DpopProver": "sincpro_framework.auth.client.adapters.dpop_prover",
    "PrivateKeyJwt": "sincpro_framework.auth.client.adapters.private_key_jwt",
}
"""Names of the `[jwt]` extra, by the module that holds them — loaded on first use."""

if TYPE_CHECKING:
    from sincpro_framework.auth.client.adapters.dpop_prover import DpopProver
    from sincpro_framework.auth.client.adapters.private_key_jwt import PrivateKeyJwt
    from sincpro_framework.auth.client.infrastructure.sealing import AesGcmSealer


def __getattr__(name: str) -> Any:
    """A name of the `[jwt]` extra, imported now — `ImportError` naming the extra when it is
    not installed."""
    if name in _LAZY:
        return getattr(importlib.import_module(_LAZY[name]), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
