"""Tenant resolution — the tenant context of an execution, validated against what the caller
may reach.

    resolved(context, memberships=None, catalog=None)     standalone: the context says it
    resolved(context, memberships, catalog)               validated:

        caller names "acme", its memberships reach acme      ->  active acme
        caller names nothing, one membership                 ->  that one, active
        caller names nothing, several memberships            ->  nothing chosen (a read refuses)
        caller names "globex", no membership there           ->  TenantNotAllowed
        caller names a tenant the catalog holds suspended    ->  TenantNotAllowed

Context: what the caller names arrives through the execution context — a header, a claim an
identity provider issued, a selector of the frontend, a message of a queue — and is checked
here at the entrance of every bus, the remote one included: a value carried from another
service is a value the caller wrote, and is judged as one.
"""

from typing import Any

from sincpro_framework.context.domain.keys import ContextKey
from sincpro_framework.tenancy.domain.catalog import TenantCatalog
from sincpro_framework.tenancy.domain.exceptions import TenantNotAllowed
from sincpro_framework.tenancy.domain.memberships import Memberships
from sincpro_framework.tenancy.domain.tenant_context import TenantContext


def allowed_tenants(
    memberships: Memberships | None, subject: str | None
) -> frozenset[str] | None:
    """The tenants `subject` may select — `None` when nothing says (standalone, or nobody known)."""
    if memberships is None or subject is None:
        return None
    return frozenset(memberships.tenants_of(subject))


def _refuse_inactive(catalog: TenantCatalog | None, tenants: TenantContext) -> None:
    """A selected tenant the catalog does not hold active is never served."""
    if catalog is None:
        return
    for one in tenants.selected:
        found = catalog.get(one)
        if found is None or not found.is_active:
            state = "unknown" if found is None else found.status.value
            raise TenantNotAllowed(f"the tenant {one!r} is {state}")


def resolved(
    context: Any,
    memberships: Memberships | None = None,
    catalog: TenantCatalog | None = None,
) -> TenantContext:
    """The tenant context of the execution `context` holds, validated — written back to it when
    resolution chose the tenant.

    1. What the caller named: `tenant_id` active, `tenant_ids` selected.
    2. Without memberships, or no subject (`user_id`) to ask them for: as named.
    3. Named outside the memberships: `TenantNotAllowed`.
    4. Nothing named and exactly one membership: that tenant, written as `tenant_id`.
    5. Every selected tenant active in the catalog, when there is one.
    Final: the tenant context.
    """
    tenants = TenantContext.of(context)
    allowed = allowed_tenants(memberships, context.get(ContextKey.USER_ID))
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
    _refuse_inactive(catalog, tenants)
    return tenants
