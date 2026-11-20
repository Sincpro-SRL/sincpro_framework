"""What this service presents when it calls another: static credentials, client credentials,
a credential per tenant."""

from sincpro_framework.auth.adapters.outbound.client_credentials import ClientCredentials
from sincpro_framework.auth.adapters.outbound.device_code import DeviceCode, DevicePrompt
from sincpro_framework.auth.adapters.outbound.exchange import TokenExchange
from sincpro_framework.auth.adapters.outbound.held_grants import (
    InMemoryHeldGrants,
    KeyValueHeldGrants,
)
from sincpro_framework.auth.adapters.outbound.per_tenant import TokenSources
from sincpro_framework.auth.adapters.outbound.refreshing import RefreshingToken
from sincpro_framework.auth.adapters.outbound.signing import RequestSigner
from sincpro_framework.auth.adapters.outbound.static import (
    ApiKeyHeader,
    BasicCredentials,
    StaticToken,
)

__all__ = [
    "ApiKeyHeader",
    "BasicCredentials",
    "ClientCredentials",
    "DeviceCode",
    "DevicePrompt",
    "InMemoryHeldGrants",
    "KeyValueHeldGrants",
    "RefreshingToken",
    "RequestSigner",
    "StaticToken",
    "TokenExchange",
    "TokenSources",
]
