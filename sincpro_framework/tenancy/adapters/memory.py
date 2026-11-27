"""The ports of tenancy held in memory — for tests, for a service whose tenants are written in
its settings, and as the second implementation every port has."""

from collections.abc import Iterable, Mapping

from sincpro_framework.tenancy.domain.catalog import Tenant, TenantCatalog, TenantStatus
from sincpro_framework.tenancy.domain.memberships import Membership, Memberships


class InMemoryTenantCatalog(TenantCatalog):
    """Tenants held in a dict.

    InMemoryTenantCatalog(Tenant("acme"), Tenant("globex", status=TenantStatus.SUSPENDED))
    InMemoryTenantCatalog.of("acme", "globex")       every one active
    """

    def __init__(self, *tenants: Tenant) -> None:
        self._tenants: dict[str, Tenant] = {one.id: one for one in tenants}

    @staticmethod
    def of(*ids: str) -> "InMemoryTenantCatalog":
        """A catalog of active tenants, by id."""
        return InMemoryTenantCatalog(*(Tenant(one) for one in ids))

    def put(self, tenant: Tenant) -> None:
        """Adds the tenant, or replaces the one of the same id — a status change included."""
        self._tenants[tenant.id] = tenant

    def get(self, tenant_id: str) -> Tenant | None:
        return self._tenants.get(tenant_id)

    def ids(self, status: TenantStatus = TenantStatus.ACTIVE) -> list[str]:
        return [one.id for one in self._tenants.values() if one.status is status]


class InMemoryMemberships(Memberships):
    """Memberships held in a dict.

    InMemoryMemberships({"ana": ["acme", "globex"], "bob": {"acme": "admin"}})
    """

    def __init__(
        self,
        memberships: Mapping[str, Iterable[str] | Mapping[str, str | None]] | None = None,
    ) -> None:
        self._by_subject: dict[str, dict[str, Membership]] = {}
        for subject, tenants in (memberships or {}).items():
            roles = tenants if isinstance(tenants, Mapping) else dict.fromkeys(tenants)
            for tenant, role in roles.items():
                self.join(subject, tenant, role)

    def join(self, subject: str, tenant: str, role: str | None = None) -> None:
        """Makes `subject` a member of `tenant`, with `role` — replacing the role it had there."""
        self._by_subject.setdefault(subject, {})[tenant] = Membership(tenant, role)

    def leave(self, subject: str, tenant: str) -> None:
        """Takes `subject` out of `tenant`."""
        self._by_subject.get(subject, {}).pop(tenant, None)

    def tenants_of(self, subject: str) -> Mapping[str, Membership]:
        return dict(self._by_subject.get(subject, {}))
