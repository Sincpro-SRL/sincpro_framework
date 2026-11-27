"""How an aggregate is partitioned by tenant — the *tenant discriminator* (Hibernate's word for
the column that tells one tenant's rows from another's in a shared table).

    TenantDiscriminator("tenant_id")                         every row belongs to one tenant
    TenantDiscriminator("company_id", Shared.WHEN_EMPTY)     an empty company is everyone's
    TenantDiscriminator("tenant_id", criteria=seen_by)       a rule of the project's own

Nothing here requires a base class: an aggregate with the field is discriminated by convention,
one without it is declared (`tenancy.discriminator(Project, ...)`).
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from sincpro_framework.ddd.criteria import Criteria

if TYPE_CHECKING:
    from sincpro_framework.tenancy.domain.tenant_context import TenantContext


class Shared(StrEnum):
    """Whether a row with no tenant is everyone's."""

    NEVER = "never"
    """Every row belongs to one tenant; a row with none is read by nobody."""
    WHEN_EMPTY = "when_empty"
    """A row with no tenant is shared: every tenant reads it (Odoo's `company_id = False`)."""


@dataclass(frozen=True)
class TenantDiscriminator:
    """The field of an aggregate that names its tenant, and how a read is narrowed by it."""

    field: str = "tenant_id"
    shared: Shared = Shared.NEVER
    criteria: "Callable[[TenantContext], Criteria] | None" = None
    """A rule of the project's own, in place of `field IN selected` — a record shared with
    other tenants through a list, a tenant reached through a relation. Writes are judged by it
    too."""
