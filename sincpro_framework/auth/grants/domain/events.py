"""What changed in who holds which — every assignment, entitlement and role defined `Grants`
writes, published when it was given a publisher.

    grants = Grants(catalog, publisher=audit)
    grants.assign("user:ana", "billing.manager", tenant="bo")
        ->  RoleAssigned(entity_id="user:ana", tenant="bo", role="billing.manager",
                         actor="user:admin")
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sincpro_framework.auth.domain.events import AuthEvent
from sincpro_framework.ddd.events import DeliverableEventMixin


@dataclass(kw_only=True)
class RoleAssigned(DeliverableEventMixin, AuthEvent):
    """A role given to a subject in a tenant — until `valid_until`, when temporary."""

    name = "auth.role.v1.assigned"
    label: dict[str, str] = field(
        default_factory=lambda: {"default": "Role assigned", "es": "Se asignó un rol"}
    )
    tenant: str | None = None
    role: str = ""
    valid_until: datetime | None = None


@dataclass(kw_only=True)
class RoleUnassigned(DeliverableEventMixin, AuthEvent):
    """A role taken back from a subject in a tenant."""

    name = "auth.role.v1.unassigned"
    label: dict[str, str] = field(
        default_factory=lambda: {"default": "Role taken away", "es": "Se quitó un rol"}
    )
    tenant: str | None = None
    role: str = ""


@dataclass(kw_only=True)
class LevelChanged(DeliverableEventMixin, AuthEvent):
    """The one level a subject holds of a privilege — `level` empty for no access."""

    name = "auth.privilege.v1.level_changed"
    label: dict[str, str] = field(
        default_factory=lambda: {
            "default": "Access level changed",
            "es": "Se cambió el nivel",
        }
    )
    tenant: str | None = None
    privilege: str = ""
    level: str | None = None


@dataclass(kw_only=True)
class TenantEntitled(DeliverableEventMixin, AuthEvent):
    """A tenant has an entitlement from now on — what it bought."""

    name = "auth.entitlement.v1.granted"
    label: dict[str, str] = field(
        default_factory=lambda: {"default": "Entitlement granted", "es": "Se habilitó"}
    )
    entitlement: str = ""


@dataclass(kw_only=True)
class EntitlementWithdrawn(DeliverableEventMixin, AuthEvent):
    """A tenant no longer has an entitlement."""

    name = "auth.entitlement.v1.withdrawn"
    label: dict[str, str] = field(
        default_factory=lambda: {"default": "Entitlement withdrawn", "es": "Se deshabilitó"}
    )
    entitlement: str = ""


ROLE = "auth.role"
"""The `entity_type` of a fact about a role defined at runtime — `entity_id` its key."""


@dataclass(kw_only=True)
class RoleDefined(DeliverableEventMixin, AuthEvent):
    """A role defined at runtime — new: its whole shape."""

    name = "auth.role.v1.defined"
    label: dict[str, str] = field(
        default_factory=lambda: {"default": "Role defined", "es": "Se definió un rol"}
    )
    tenant: str | None = None
    """The tenant it exists for — `None`: every tenant."""
    role_name: str = ""
    grants: list[str] = field(default_factory=list)
    implies: list[str] = field(default_factory=list)
    privilege: str | None = None
    sequence: int = 0
    disjoint: list[str] = field(default_factory=list)


@dataclass(kw_only=True)
class RoleChanged(DeliverableEventMixin, AuthEvent):
    """A role defined at runtime changed — its whole shape now, and before."""

    name = "auth.role.v1.changed"
    label: dict[str, str] = field(
        default_factory=lambda: {"default": "Role changed", "es": "Se cambió un rol"}
    )
    tenant: str | None = None
    """The tenant it exists for — `None`: every tenant."""
    role_name: str = ""
    grants: list[str] = field(default_factory=list)
    implies: list[str] = field(default_factory=list)
    privilege: str | None = None
    sequence: int = 0
    disjoint: list[str] = field(default_factory=list)
    previous: dict[str, Any] = field(default_factory=dict)
    """What it was: `role_name`, `grants`, `implies`, `privilege`, `sequence`, `disjoint`."""


@dataclass(kw_only=True)
class RoleRemoved(DeliverableEventMixin, AuthEvent):
    """A role defined at runtime removed — its holders' assignments grant nothing from now."""

    name = "auth.role.v1.removed"
    label: dict[str, str] = field(
        default_factory=lambda: {"default": "Role removed", "es": "Se eliminó un rol"}
    )
    tenant: str | None = None
