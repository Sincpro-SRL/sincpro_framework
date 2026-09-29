"""The providers and helpers the framework ships. Any class that honours `AuthProvider` stands
beside them — a project's wrapper of its IdP, its Odoo, its policy engine."""

from sincpro_framework.auth.adapters.role_permissions import RolePermissions
from sincpro_framework.auth.adapters.static_provider import StaticProvider

__all__ = ["RolePermissions", "StaticProvider"]
