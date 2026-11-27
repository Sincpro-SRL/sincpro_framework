"""Tenant resolution — the tenant context of an execution, validated against what the caller
may reach.

    caller names "acme", its memberships reach acme      ->  active acme
    caller names nothing, one membership                 ->  that one, active
    caller names nothing, several memberships            ->  nothing chosen
    caller names "globex", no membership there           ->  TenantNotAllowed
    caller crosses tenants (`cross_tenant`)              ->  any tenant, memberships not asked
    caller names a tenant the catalog holds suspended    ->  TenantNotAllowed

Context: what the caller names arrives through the execution context — a header, a claim, a
selector, a message of a queue — and is checked at the entrance of every bus, a remote one
included: a value carried from another service is a value the caller wrote.
"""

from collections.abc import Callable
from typing import Any

from sincpro_framework.context.domain.keys import ContextKey
from sincpro_framework.tenancy.domain.catalog import TenantCatalog
from sincpro_framework.tenancy.domain.exceptions import (
    TenantNotAllowed,
    TenantNotChosen,
    TenantRequired,
)
from sincpro_framework.tenancy.domain.memberships import Memberships
from sincpro_framework.tenancy.domain.tenant_context import TenantContext

type CrossTenant = Callable[[], bool]
"""Whether the caller in play may work in any tenant — a support engineer, the platform: what
auth answers (`lambda: auth.allows(Platform.SUPPORT)`), tenancy never decides it."""


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

    def crosses(self) -> bool:
        """Whether the caller in play may work in any tenant."""
        return self.cross_tenant is not None and bool(self.cross_tenant())

    def allowed(self, context: Any) -> frozenset[str] | None:
        """The tenants the caller may select — `None` when nothing limits it: no memberships,
        nobody known (`user_id`), or a caller that crosses tenants."""
        subject = context.get(ContextKey.USER_ID)
        if self.memberships is None or subject is None or self.crosses():
            return None
        return frozenset(self.memberships.tenants_of(subject))

    def _refuse_inactive(self, tenants: TenantContext) -> None:
        """A selected tenant the catalog does not hold active is never served."""
        if self.catalog is None:
            return
        for one in tenants.selected:
            found = self.catalog.get(one)
            if found is None or not found.is_active:
                state = "unknown" if found is None else found.status.value
                raise TenantNotAllowed(f"the tenant {one!r} is {state}")

    def allows(self, context: Any, tenant: str) -> bool:
        """Whether the caller may work in `tenant` — asked without raising, as `auth.allows`."""
        allowed = self.allowed(context)
        if allowed is not None and tenant not in allowed:
            return False
        if self.catalog is not None:
            found = self.catalog.get(tenant)
            return found is not None and found.is_active
        return True

    def resolved(self, context: Any) -> TenantContext:
        """The tenant context `context` holds, validated — written back when resolution chose it.

        1. What the caller named: `tenant_id` active, `tenant_ids` selected.
        2. Named outside what the caller may select: `TenantNotAllowed`.
        3. Nothing named and exactly one membership: that tenant, written as `tenant_id`.
        4. Every selected tenant active in the catalog, when there is one.
        Final: the tenant context.
        """
        tenants = TenantContext.of(context)
        allowed = self.allowed(context)
        if allowed is not None:
            outside = [one for one in tenants.selected if one not in allowed]
            if outside:
                raise TenantNotAllowed(
                    f"{context.get(ContextKey.USER_ID)} is no member of {', '.join(outside)}"
                )
            if tenants.is_empty and len(allowed) == 1:
                only = next(iter(allowed))
                context.set(ContextKey.TENANT_ID, only)
                tenants = TenantContext(only, (only,))
        self._refuse_inactive(tenants)
        return tenants

    def required(self, context: Any, use_case: str) -> TenantContext:
        """The tenant context, with an active tenant — `TenantNotChosen` for a member of several
        who named none, `TenantRequired` otherwise."""
        tenants = self.resolved(context)
        if tenants.active is None:
            allowed = self.allowed(context)
            refusal = (
                TenantNotChosen
                if allowed is not None and len(allowed) > 1
                else TenantRequired
            )
            raise refusal(f"{use_case} runs in one tenant and this execution names none")
        return tenants
