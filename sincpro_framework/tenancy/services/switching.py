"""Switching tenant — the use case of working in other tenants for a block: one (ABP's
`CurrentTenant.Change`, acts_as_tenant's `with_tenant`) or a sub-selection, validated against the
caller as an entrance is.

    with working_in(resolution, ["acme"], "acme"):
        billing(CommandIssueInvoice(...))
"""

from collections.abc import Generator, Iterable
from contextlib import contextmanager

from sincpro_framework.context import use_context
from sincpro_framework.context.domain.keys import ContextKey
from sincpro_framework.tenancy.domain.tenant_context import TenantContext
from sincpro_framework.tenancy.services.resolution import Resolution


@contextmanager
def working_in(
    resolution: Resolution, tenants: Iterable[str], active: str | None = None
) -> Generator[TenantContext]:
    """The block works in `tenants`, writing to `active` — the only one when one is named."""
    selected = list(dict.fromkeys(tenants))
    if active is None and len(selected) == 1:
        active = selected[0]
    values = {ContextKey.TENANT_IDS: selected, ContextKey.TENANT_ID: active}
    with use_context().scoped(values) as scope:
        yield resolution.resolved(scope)
