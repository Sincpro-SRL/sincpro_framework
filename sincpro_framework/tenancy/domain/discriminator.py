"""How an aggregate is partitioned by tenant: the tenant discriminator, Hibernate's name for the
field that tells one tenant's rows from another's.

    TenantDiscriminator("tenant_id")                         every row belongs to one tenant
    TenantDiscriminator("company_id", Shared.WHEN_EMPTY)     an empty company is everyone's
    TenantDiscriminator("tenant_id", criteria=seen_by)       a rule of the project's own

Context: nothing is partitioned by convention — an aggregate is partitioned because it was
declared (`tenancy.discriminator(...)`) or inherits `TenantOwnedMixin`.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sincpro_framework.ddd.criteria import Criteria

if TYPE_CHECKING:
    from sincpro_framework.tenancy.domain.tenant_context import TenantContext


class Shared(StrEnum):
    """Whether a row with no tenant is everyone's."""

    NEVER = "never"
    WHEN_EMPTY = "when_empty"
    """Odoo's `company_id = False`: every tenant reads it."""


class WhenMissing(StrEnum):
    """What an isolated use case does when the execution names no tenant — auth's `WhenDenied`,
    for tenants."""

    RAISE = "raise"
    """`TenantRequired`."""
    SKIP = "skip"
    """The use case does not run and answers `None`. A use case only: a block cannot skip its
    own body."""
    SHARED = "shared"
    """Only what belongs to no tenant is read, and a new record belongs to none."""


@dataclass(frozen=True)
class TenantDiscriminator:
    """The field of an aggregate that names its tenant."""

    field: str = "tenant_id"
    shared: Shared = Shared.NEVER
    criteria: "Callable[[TenantContext], Criteria] | None" = None
    """A rule of the project's own in place of `field IN selected`; writes are judged by it."""
    empty: Any = None
    """What the field holds for no tenant — `None`, or `""` for a column that is never NULL."""
