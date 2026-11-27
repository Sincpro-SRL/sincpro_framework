"""The use cases of tenancy, built in — DTOs to subclass and one Feature that answers them, the
way `EntityReads` answers an entity's reads. A project registers what it wants, guarded as it
guards anything else:

    class QueryMyTenants(GetTenantContext): ...
    class CommandAddMember(JoinTenant): ...

    @accounts.feature([QueryMyTenants, CommandAddMember])
    @auth.requires(AccountsPermission.ADMINISTER)      # the project's rule, for the writes
    class Tenants(TenantUseCases): ...

| DTO | Answer | What it does |
|---|---|---|
| `GetTenantContext` | `ResponseTenantContext` | the caller's active, selected and allowed tenants — a frontend's tenant selector |
| `ListTenants` | `ResponseTenants` | the catalog's tenants in a status |
| `PutTenant` | `ResponseTenant` | adds a tenant, or changes its name or status (suspend, reactivate, offboard) |
| `ListTenantMembers` | `ResponseTenantMembers` | the subjects of a tenant, with their role |
| `JoinTenant` / `LeaveTenant` | `ResponseMembership` | a subject joins or leaves a tenant |

Each reads and writes the ports `Tenancy` was given — the catalog and the memberships, in
memory, on a database or in a system of the project's own.
"""

from typing import TYPE_CHECKING, Any

from sincpro_framework.exceptions import ProgrammingError
from sincpro_framework.sincpro_abstractions import DataTransferObject, Feature
from sincpro_framework.tenancy.domain.catalog import Tenant, TenantCatalog, TenantStatus
from sincpro_framework.tenancy.domain.memberships import Memberships

if TYPE_CHECKING:
    from sincpro_framework.tenancy.entrypoint.tenancy import Tenancy


class GetTenantContext(DataTransferObject):
    """The caller's tenant context."""


class ResponseTenant(DataTransferObject):
    """One tenant of the catalog."""

    id: str
    name: str = ""
    status: TenantStatus = TenantStatus.ACTIVE

    @staticmethod
    def of(tenant: Tenant) -> "ResponseTenant":
        return ResponseTenant(id=tenant.id, name=tenant.name, status=tenant.status)


class ResponseTenantContext(DataTransferObject):
    """Where the caller works: the tenant a write goes to, what a read sees, what it may select
    — `allowed` empty when nothing says (no memberships, or nobody known)."""

    active: str | None = None
    selected: list[str] = []
    allowed: list[str] = []
    tenant: ResponseTenant | None = None
    """The active tenant as the catalog knows it."""


class ListTenants(DataTransferObject):
    status: TenantStatus = TenantStatus.ACTIVE


class ResponseTenants(DataTransferObject):
    tenants: list[ResponseTenant] = []


class PutTenant(DataTransferObject):
    id: str
    name: str = ""
    status: TenantStatus = TenantStatus.ACTIVE


class ListTenantMembers(DataTransferObject):
    tenant: str


class ResponseTenantMembers(DataTransferObject):
    tenant: str
    members: dict[str, str | None] = {}
    """Each subject, with its role."""


class JoinTenant(DataTransferObject):
    subject: str
    tenant: str
    role: str | None = None


class LeaveTenant(DataTransferObject):
    subject: str
    tenant: str


class ResponseMembership(DataTransferObject):
    subject: str
    tenant: str
    role: str | None = None
    member: bool = True


class TenantUseCases(Feature):
    """The Feature that answers every built-in use case of tenancy, each overridable alone."""

    tenancy: "Tenancy"
    """Injected by `tenancy.on(bus)`."""

    def _catalog(self) -> TenantCatalog:
        """The catalog of the bus's `Tenancy`."""
        catalog = self.tenancy.catalog
        if catalog is None:
            raise ProgrammingError(
                f"{type(self).__name__} reads the tenant catalog and Tenancy has none — "
                "Tenancy(catalog=...)"
            )
        return catalog

    def _memberships(self) -> Memberships:
        """The memberships of the bus's `Tenancy`."""
        memberships = self.tenancy.memberships
        if memberships is None:
            raise ProgrammingError(
                f"{type(self).__name__} reads the memberships and Tenancy has none — "
                "Tenancy(memberships=...)"
            )
        return memberships

    def get_tenant_context(self, dto: GetTenantContext) -> ResponseTenantContext:
        tenancy = self.tenancy
        tenant = tenancy.tenant()
        return ResponseTenantContext(
            active=tenancy.active,
            selected=list(tenancy.selected),
            allowed=sorted(tenancy.allowed or ()),
            tenant=None if tenant is None else ResponseTenant.of(tenant),
        )

    def list_tenants(self, dto: ListTenants) -> ResponseTenants:
        catalog = self._catalog()
        found = (catalog.get(one) for one in catalog.ids(dto.status))
        return ResponseTenants(tenants=[ResponseTenant.of(one) for one in found if one])

    def put_tenant(self, dto: PutTenant) -> ResponseTenant:
        tenant = Tenant(dto.id, dto.name, dto.status)
        self._catalog().put(tenant)
        return ResponseTenant.of(tenant)

    def list_tenant_members(self, dto: ListTenantMembers) -> ResponseTenantMembers:
        return ResponseTenantMembers(
            tenant=dto.tenant, members=self._memberships().members_of(dto.tenant)
        )

    def join_tenant(self, dto: JoinTenant) -> ResponseMembership:
        self._memberships().join(dto.subject, dto.tenant, dto.role)
        return ResponseMembership(subject=dto.subject, tenant=dto.tenant, role=dto.role)

    def leave_tenant(self, dto: LeaveTenant) -> ResponseMembership:
        self._memberships().leave(dto.subject, dto.tenant)
        return ResponseMembership(subject=dto.subject, tenant=dto.tenant, member=False)

    def execute(self, dto: Any) -> Any:
        """Routes the DTO to the use case its base names."""
        match dto:
            case GetTenantContext():
                return self.get_tenant_context(dto)
            case ListTenants():
                return self.list_tenants(dto)
            case PutTenant():
                return self.put_tenant(dto)
            case ListTenantMembers():
                return self.list_tenant_members(dto)
            case JoinTenant():
                return self.join_tenant(dto)
            case LeaveTenant():
                return self.leave_tenant(dto)
        raise ProgrammingError(
            f"{type(self).__name__} answers the use cases of tenancy, not {type(dto).__name__}"
        )
