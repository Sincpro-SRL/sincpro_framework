"""Isolation — the use case of working inside the tenant context: every read narrowed to the
selected tenants, every new record stamped with the active one, every write outside answered as
not found. One use case; a decorator, a block and a repository view are doors to it.

    with isolated(discriminators, WhenMissing.RAISE):
        repository.search(Invoices)           the tenant's

Context: the tenant filter is applied to the execution, not to a store (`DataFilters.applied`):
it lives in the execution context, so the nearest scope decides — a block that crosses tenants
inside it reads them all, one isolated inside that is isolated again —, a nested Feature, a hook,
a thread or a task handed the context inherit it, and another service never receives it: the
tenant travels, the isolation is declared where the code runs.
"""

from collections.abc import Generator
from contextlib import contextmanager

from sincpro_framework.context import use_context
from sincpro_framework.ddd.repositories.data_filters import DataFilters, DataFilterState
from sincpro_framework.tenancy.domain.discriminator import WhenMissing
from sincpro_framework.tenancy.domain.exceptions import TenantRequired
from sincpro_framework.tenancy.domain.tenant_context import TenantContext
from sincpro_framework.tenancy.services.discrimination import (
    TENANT_FILTER,
    Discriminators,
    TenantFilter,
)


def decided() -> bool:
    """Whether the execution in play already decided about tenants — isolated, or crossing them:
    what a bus isolated by default leaves alone, so a nested use case inherits its caller."""
    state = DataFilterState.current()
    return TENANT_FILTER in state.disabled or any(
        one.key == TENANT_FILTER for one in state.applied
    )


def skips(when_missing: WhenMissing) -> bool:
    """Whether a use case isolated with `when_missing` does not run here: it skips, and no
    tenant is named."""
    return when_missing is WhenMissing.SKIP and TenantContext.of(use_context()).is_empty


@contextmanager
def isolated(
    discriminators: Discriminators, when_missing: WhenMissing = WhenMissing.RAISE
) -> Generator[TenantContext]:
    """The block works inside the tenant context.

    1. No tenant named: `TenantRequired` — unless what is missing reads the shared; a block
       cannot skip its body, so `SKIP` reads as `RAISE` here.
    Final: the tenant filter applied to the execution for the block.
    """
    tenants = TenantContext.of(use_context())
    if tenants.is_empty and when_missing is not WhenMissing.SHARED:
        raise TenantRequired(
            "this work is isolated in a tenant and the execution names none — name one "
            "(tenant_id), or cross tenants with without_tenant"
        )
    with DataFilters.applied(TenantFilter(discriminators, when_missing)):
        yield tenants
