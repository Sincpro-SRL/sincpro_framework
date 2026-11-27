"""Crossing tenants — the use case of working across every tenant, asked by name and recorded:
a consolidated report, a migration, a platform's support.

    with across_tenants("consolidated report"):
        repository.search(Invoices)            every tenant's

Context: whether the caller may is auth's to decide (`@auth.requires`); that the code crosses is
declared here, with its reason, and logged — never silent.
"""

from collections.abc import Generator
from contextlib import contextmanager

from sincpro_framework.context import use_context
from sincpro_framework.context.domain.keys import ContextKey
from sincpro_framework.ddd.repositories.data_filters import DataFilters
from sincpro_framework.sincpro_logger import logger
from sincpro_framework.tenancy.services.discrimination import TENANT_FILTER


@contextmanager
def across_tenants(reason: str) -> Generator[None]:
    """The block reads and writes across tenants — the tenant filter off, the crossing logged
    with its reason and who crossed."""
    logger.info(
        "across tenants", reason=reason, user_id=use_context().get(ContextKey.USER_ID)
    )
    with DataFilters.disabled(TENANT_FILTER):
        yield
