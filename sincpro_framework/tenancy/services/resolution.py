"""Tenant resolution: the tenant context of an execution, validated against what the caller may
reach.

    caller names "acme", a member of acme             ->  active acme
    caller names nothing, member of one tenant        ->  that one, active
    caller names "globex", no membership there        ->  TenantNotAllowed
    caller may cross tenants (`cross_tenant`)         ->  any tenant
    caller names a tenant the catalog holds suspended ->  TenantNotAllowed

Context: validated once per execution and never cached across them — a member taken out of a
tenant, or a tenant suspended, is refused from the next request on.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sincpro_framework.context.domain.keys import ContextKey
from sincpro_framework.tenancy.domain.catalog import TenantCatalog
from sincpro_framework.tenancy.domain.exceptions import TenantNotAllowed, TenantRequired
from sincpro_framework.tenancy.domain.memberships import Memberships
from sincpro_framework.tenancy.domain.tenant_context import TenantContext

type CrossTenant = Callable[[], bool]
"""Whether the caller in play may work in any tenant — auth's to answer
(`lambda: auth.allows(P.SUPPORT)`), never tenancy's."""


@dataclass(frozen=True)
class Validated:
    """The tenant context this execution validated — a typed key of the execution context, so a
    nested call in the same tenants is not validated again, and nothing outlives the execution.
    """

    tenants: TenantContext


class Resolution:
    """The ports and the policy a tenant context is validated with."""

    def __init__(
        self,
        memberships: Memberships | None = None,
        catalog: TenantCatalog | None = None,
        cross_tenant: CrossTenant | None = None,
    ) -> None:
        self.memberships = memberships
        self.catalog = catalog
        self.cross_tenant = cross_tenant

    def allowed(self, context: Any) -> frozenset[str] | None:
        """The tenants the caller may select — `None` when nothing limits it: no memberships,
        nobody known (`user_id`), or a caller that crosses tenants."""
        subject = context.get(ContextKey.USER_ID)
        crosses = self.cross_tenant is not None and bool(self.cross_tenant())
        if self.memberships is None or subject is None or crosses:
            return None
        return frozenset(self.memberships.tenants_of(subject))

    def _refuse_inactive(self, tenants: TenantContext) -> None:
        if self.catalog is None:
            return
        for one in tenants.selected:
            found = self.catalog.get(one)
            if found is None or not found.is_active:
                state = "unknown" if found is None else found.status.value
                raise TenantNotAllowed(f"the tenant {one!r} is {state}")

    def allows(self, context: Any, tenant: str) -> bool:
        """Whether the caller may work in `tenant`, asked without raising."""
        allowed = self.allowed(context)
        if allowed is not None and tenant not in allowed:
            return False
        if self.catalog is None:
            return True
        found = self.catalog.get(tenant)
        return found is not None and found.is_active

    def resolved(self, context: Any) -> TenantContext:
        """Context: what the caller named, validated — and chosen for it when it belongs to one
        tenant only. A context this execution validated already is answered as it is.

        1. Validated already in this execution, for these tenants: answered.
        2. Named outside what the caller may select: `TenantNotAllowed`.
        3. Nothing named and one membership: that tenant, written as `tenant_id`.
        4. Every selected tenant active in the catalog.
        Final: the tenant context, marked validated for the rest of the execution.
        """
        tenants = TenantContext.of(context)
        if context.get(Validated) == Validated(tenants):
            return tenants
        allowed = self.allowed(context)
        if allowed is not None:
            outside = [one for one in tenants.selected if one not in allowed]
            if outside:
                raise TenantNotAllowed(
                    f"{context.get(ContextKey.USER_ID)} is no member of {', '.join(outside)}"
                )
            if tenants.is_empty and len(allowed) == 1:
                tenants = TenantContext(next(iter(allowed)), tuple(allowed))
                context.set(ContextKey.TENANT_ID, tenants.active)
        self._refuse_inactive(tenants)
        context.set(Validated, Validated(tenants))
        return tenants

    def refuse_without_active(
        self, context: Any, tenants: TenantContext, use_case: str
    ) -> None:
        """`TenantRequired` when no tenant is active — saying so to a member of several, who
        has to choose."""
        if tenants.active is not None:
            return
        allowed = self.allowed(context)
        if allowed is not None and len(allowed) > 1:
            raise TenantRequired(
                f"{use_case} runs in one tenant and you belong to several — choose one "
                "(tenant_id)"
            )
        raise TenantRequired(f"{use_case} runs in one tenant and this execution names none")
