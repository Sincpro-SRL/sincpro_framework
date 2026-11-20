"""What this service presents when it calls another: static credentials, client credentials,
a credential per tenant."""

from sincpro_framework.auth.adapters.outbound.client_credentials import ClientCredentials
from sincpro_framework.auth.adapters.outbound.per_tenant import TokenSources
from sincpro_framework.auth.adapters.outbound.static import (
    ApiKeyHeader,
    BasicCredentials,
    StaticToken,
)

__all__ = [
    "ApiKeyHeader",
    "BasicCredentials",
    "ClientCredentials",
    "StaticToken",
    "TokenSources",
]
