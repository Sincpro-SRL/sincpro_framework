"""What changed in who holds which — every assignment and entitlement `GrantAdministration`
writes, published when it was given a publisher.

    admin = GrantAdministration(catalog, grants, revocations, publisher=audit)
    admin.assign("user:ana", "bo", "billing.manager")
        ->  RoleAssigned(entity_id="user:ana", tenant="bo", role="billing.manager",
                         actor="user:admin")
"""

from dataclasses import dataclass, field
from datetime import datetime

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
