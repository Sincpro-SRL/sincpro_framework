"""The tenant catalog — the registry of tenants a service knows: who exists, in which state of
its lifecycle, and where its data lives (Azure's *tenant catalog*).

    catalog.get("acme")            ->  Tenant(id="acme", status=TenantStatus.ACTIVE)
    catalog.ids()                  ->  ["acme", "globex"]       the active ones

A port: a service with a table, an Odoo of its own or a control plane answers it with its own
adapter; `InMemoryTenantCatalog` and the SQL recipe are the ones shipped.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TenantStatus(StrEnum):
    """Where a tenant stands in its lifecycle — the technical state, apart from any commercial one."""

    PROVISIONING = "provisioning"
    """Being set up: its data is not ready to be served."""
    ACTIVE = "active"
    SUSPENDED = "suspended"
    """Kept, and served to nobody until it is reactivated."""
    OFFBOARDED = "offboarded"
    """Gone or leaving: its data is retained or deleted, never served."""


@dataclass(frozen=True)
class Tenant:
    """One tenant as the catalog knows it."""

    id: str
    name: str = ""
    status: TenantStatus = TenantStatus.ACTIVE
    placement: Mapping[str, Any] = field(default_factory=dict)
    """Where its data lives, as the project describes it — a schema, a database URL, a region.
    Read by a project's own routing; the framework reads nothing of it."""

    @property
    def is_active(self) -> bool:
        """Whether it is served."""
        return self.status is TenantStatus.ACTIVE


class TenantCatalog(ABC):
    """The tenants a service knows. `get` and `ids` are the whole contract."""

    @abstractmethod
    def get(self, tenant_id: str) -> Tenant | None:
        """The tenant, or `None` when the catalog does not know it."""

    @abstractmethod
    def ids(self, status: TenantStatus = TenantStatus.ACTIVE) -> list[str]:
        """The ids of the tenants in `status` — what a job run per tenant iterates."""
