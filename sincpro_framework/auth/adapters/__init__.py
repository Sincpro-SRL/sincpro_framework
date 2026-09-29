"""The providers and helpers the framework ships. Any class that honours `AuthProvider` stands
beside them — a project's wrapper of its IdP, its Odoo, its policy engine."""

from sincpro_framework.auth.adapters.api_key_provider import (
    ApiKey,
    ApiKeyProvider,
    ApiKeyStore,
    InMemoryApiKeys,
)
from sincpro_framework.auth.adapters.role_permissions import RolePermissions
from sincpro_framework.auth.adapters.service_token_provider import ServiceTokenProvider
from sincpro_framework.auth.adapters.static_provider import StaticProvider

__all__ = [
    "ApiKey",
    "ApiKeyProvider",
    "ApiKeyStore",
    "InMemoryApiKeys",
    "RolePermissions",
    "ServiceTokenProvider",
    "StaticProvider",
]
