"""Tenant resolution: the tenant context of an execution, validated against what the caller may
reach.

    caller names "acme", a member of acme             ->  active acme
    caller names nothing, member of one tenant        ->  that one, active
    caller names "globex", no membership there        ->  TenantNotAllowed
    anonymous caller names any tenant                 ->  TenantNotAllowed
    caller may cross tenants (`cross_tenant`)         ->  any tenant
    caller names a tenant the catalog holds suspended ->  TenantNotAllowed

Context: what a caller sends through a wire is judged against its memberships; what the code
says (`with_tenant`, `bus.context` in code) is the code's decision. Validated once per execution
and never cached across them: a member taken out of a tenant, or a tenant suspended, is refused
from the next request on.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sincpro_framework.context.domain.keys import ContextKey
from sincpro_framework.context.domain.level import said_by_caller
from sincpro_framework.tenancy.domain.catalog import TenantCatalog
from sincpro_framework.tenancy.domain.exceptions import TenantNotAllowed, TenantRequired
from sincpro_framework.tenancy.domain.memberships import Memberships
from sincpro_framework.tenancy.domain.tenant_context import TenantContext

type CrossTenant = Callable[[], bool]
"""Whether the caller in play may work in any tenant — auth's to answer
(`lambda: auth.allows(P.SUPPORT)`), never tenancy's."""


@dataclass(frozen=True)
class Validated:
    """The tenant context this execution validated, and for whom — a typed key of the execution
    context: a nested call by the same subject in the same tenants is not validated again, and
    nothing outlives the execution."""

    tenants: TenantContext
    subject: str | None


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
        """The tenants the caller may select — `None` when nothing limits it (no memberships, or
        a caller that crosses tenants); none for a caller nobody knows."""
        if self.memberships is None:
            return None
        if self.cross_tenant is not None and self.cross_tenant():
            return None
        subject = context.get(ContextKey.USER_ID)
        return frozenset(self.memberships.tenants_of(subject)) if subject else frozenset()

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
        tenant only.

        1. Validated already in this execution, by this subject, for these tenants: answered.
        2. Named by the caller outside what it may select: `TenantNotAllowed`.
        3. Nothing named and one membership: that tenant, written as `tenant_id`.
        4. Every selected tenant active in the catalog.
        Final: the tenant context, marked validated for the rest of the execution.
        """
        tenants = TenantContext.of(context)
        subject = context.get(ContextKey.USER_ID)
        if context.get(Validated) == Validated(tenants, subject):
            return tenants
        allowed = self.allowed(context)
        if allowed is not None:
            named = said_by_caller(context, ContextKey.TENANT_ID) or said_by_caller(
                context, ContextKey.TENANT_IDS
            )
            outside = [one for one in tenants.selected if one not in allowed] if named else []
            if outside:
                raise TenantNotAllowed(
                    f"{subject or 'nobody'} is no member of {', '.join(outside)}"
                )
            if tenants.is_empty and len(allowed) == 1:
                tenants = TenantContext(next(iter(allowed)), tuple(allowed))
                context.set(ContextKey.TENANT_ID, tenants.active)
        self._refuse_inactive(tenants)
        context.set(Validated, Validated(tenants, subject))
        return tenants

    def refuse_without_active(
        self, context: Any, tenants: TenantContext, use_case: str
    ) -> None:
        """`TenantRequired` when no tenant is active — telling a member of several to choose."""
        if tenants.active is not None:
            return
        allowed = self.allowed(context)
        if allowed is not None and len(allowed) > 1:
            raise TenantRequired(
                f"{use_case} runs in one tenant and you belong to several — choose one "
                "(tenant_id)"
            )
        raise TenantRequired(f"{use_case} runs in one tenant and this execution names none")
