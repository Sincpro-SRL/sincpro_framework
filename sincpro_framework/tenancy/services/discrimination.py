"""Which aggregates are partitioned by tenant, and the data filter that keeps an execution in its
tenants — the persistence side of tenancy.

    discriminators = Discriminators()
    discriminators.declare(Invoice, TenantDiscriminator("company_id"))
    TenantFilter(discriminators)            reads narrowed, new records stamped, writes checked

Context: nothing is partitioned by convention. An aggregate is partitioned because it was
declared, or because it inherits `TenantOwnedMixin` — a declaration written as a base class.
A field called `tenant_id` that nobody declared is a field like any other.
"""

from typing import Any

from sincpro_framework.context import use_context
from sincpro_framework.ddd.criteria import Any_, Condition, Criteria, Operator
from sincpro_framework.ddd.exceptions import AggregateNotFound
from sincpro_framework.ddd.importing import ExternalKey
from sincpro_framework.ddd.repositories.data_filters import DataFilter
from sincpro_framework.tenancy.domain.discriminator import (
    Shared,
    TenantDiscriminator,
    WhenMissing,
)
from sincpro_framework.tenancy.domain.exceptions import TenantRequired
from sincpro_framework.tenancy.domain.tenant_context import TenantContext
from sincpro_framework.tenancy.domain.tenant_owned import TenantOwnedMixin

TENANT_FILTER = "tenant"
"""The name the tenant filter is turned off by — `DataFilters.disabled(TENANT_FILTER)`."""


class Discriminators:
    """How each aggregate is partitioned — declared, inherited from `TenantOwnedMixin`, or not
    at all. The framework's own partitioned record is declared already: an external key lives
    in the scope of its tenant."""

    def __init__(self, shared: Shared = Shared.NEVER) -> None:
        self.shared = shared
        """Whether an empty tenant is everyone's, for what is declared without saying."""
        self._declared: dict[type, TenantDiscriminator | None] = {
            ExternalKey: TenantDiscriminator("scope", Shared.WHEN_EMPTY, empty="")
        }

    def declare(self, aggregate: type, discriminator: TenantDiscriminator | None) -> None:
        """How `aggregate` — and what inherits it — is partitioned; `None`: every tenant reads
        it."""
        self._declared[aggregate] = discriminator

    def of(self, aggregate: type) -> TenantDiscriminator | None:
        """How `aggregate` is partitioned: declared for it or a class it inherits, else by
        `TenantOwnedMixin`, else not at all."""
        for one in aggregate.__mro__:
            if one in self._declared:
                return self._declared[one]
        if issubclass(aggregate, TenantOwnedMixin):
            return TenantDiscriminator("tenant_id", self.shared)
        return None

    def criteria(
        self, aggregate: type, tenants: TenantContext, when_missing: WhenMissing
    ) -> Criteria | None:
        """What a read of `aggregate` in `tenants` adds — `None` when it is not partitioned.

        1. A rule of the project's own: its criteria, the tenants named.
        2. No tenant named: `TenantRequired`, or — when what is missing reads the shared —
           only the records of no tenant.
        Final: the selected tenants, and the empty ones too when an empty tenant is shared.
        """
        discriminator = self.of(aggregate)
        if discriminator is None:
            return None
        field = discriminator.field
        empty = (
            Condition(field=field, operator=Operator.IS_NULL, value=True)
            if discriminator.empty is None
            else Condition(field=field, value=discriminator.empty)
        )
        if tenants.is_empty:
            if when_missing is WhenMissing.SHARED:
                return Criteria(selection=empty)
            raise TenantRequired(
                f"{aggregate.__name__} is partitioned by tenant and this execution names none "
                "— name one (tenant_id), or read across tenants with without_tenant"
            )
        if discriminator.criteria is not None:
            return discriminator.criteria(tenants)
        selected = Condition(field=field, operator=Operator.IN, value=list(tenants.selected))
        if discriminator.shared is Shared.NEVER:
            return Criteria(selection=selected)
        return Criteria(selection=Any_(any=[selected, empty]))


class TenantFilter(DataFilter):
    """The data filter that keeps an execution in its tenants — applied by `isolated`, never
    registered on a store."""

    name = TENANT_FILTER

    def __init__(
        self, discriminators: Discriminators, when_missing: WhenMissing = WhenMissing.RAISE
    ) -> None:
        self.discriminators = discriminators
        self.when_missing = when_missing

    def criteria(self, aggregate: type) -> Criteria | None:
        """The tenant context of the execution in play, as a criteria over `aggregate`."""
        return self.discriminators.criteria(
            aggregate, TenantContext.of(use_context()), self.when_missing
        )

    def stamp(self, record: Any) -> None:
        """Context: a new record with no tenant takes the active one; with several selected and
        none active it is `TenantRequired` — unless an empty tenant is shared, and it stays
        everyone's."""
        discriminator = self.discriminators.of(type(record))
        if discriminator is None or getattr(record, discriminator.field, None):
            return
        tenants = TenantContext.of(use_context())
        if tenants.active is not None:
            setattr(record, discriminator.field, tenants.active)
        elif discriminator.shared is Shared.NEVER and not tenants.is_empty:
            raise TenantRequired(
                f"{type(record).__name__} is written to the active tenant and several are "
                "selected with none active — name the one it goes to (tenant_id)"
            )

    def refusal(self, record: Any) -> Exception:
        """Context: another tenant's record is answered as missing, never told apart from one
        that does not exist."""
        return AggregateNotFound(
            f"{type(record).__name__} {getattr(record, 'id', '')} not found"
        )
