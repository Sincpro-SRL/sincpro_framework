"""`TenantFilter` — tenancy as a data filter: every read of an aggregate partitioned by tenant
narrowed to the selected tenants, every new record stamped with the active one, every write
outside the selection refused.

    TenantFilter()                                    every aggregate with a `tenant_id`
    TenantFilter("company_id", Shared.WHEN_EMPTY)     another field; an empty one is shared
    tenant_filter.discriminate(Project, criteria=seen_by)

Context: the filter is the persistence side of tenancy and nothing else — it reads the tenant
context the execution holds and never decides who may name which tenant; resolution does.
"""

from typing import Any
from weakref import WeakKeyDictionary

from sincpro_framework.context import use_context
from sincpro_framework.ddd.criteria import Any_, Condition, Criteria, Operator
from sincpro_framework.ddd.importing import ExternalKey
from sincpro_framework.ddd.repositories.data_filters import DataFilter
from sincpro_framework.tenancy.domain.discriminator import Shared, TenantDiscriminator
from sincpro_framework.tenancy.domain.exceptions import TenantRequired
from sincpro_framework.tenancy.domain.tenant_context import TenantContext

TENANT_FILTER = "tenant"
"""The name the tenant filter is turned off by — `DataFilters.disabled(TENANT_FILTER)`."""


def _scope_or_everyone(tenants: TenantContext) -> Criteria:
    """An external key of the selected tenants, or of the namespace every tenant shares (`""`)."""
    return Criteria(
        selection=Any_(
            any=[
                Condition(field="scope", operator=Operator.IN, value=list(tenants.selected)),
                Condition(field="scope", value=""),
            ]
        )
    )


def declares(aggregate: type, field: str) -> bool:
    """Whether `aggregate` or a class it inherits declares `field`."""
    return any(field in getattr(one, "__annotations__", {}) for one in aggregate.__mro__)


class TenantFilter(DataFilter):
    """The data filter of tenancy."""

    name = TENANT_FILTER

    def __init__(self, field: str = "tenant_id", shared: Shared = Shared.NEVER) -> None:
        self.convention = TenantDiscriminator(field, shared)
        """What an aggregate with the field gets when nothing was declared for it."""
        self.declared: dict[type, TenantDiscriminator | None] = {
            ExternalKey: TenantDiscriminator("scope", Shared.WHEN_EMPTY, _scope_or_everyone)
        }
        """What was declared per aggregate — the framework's own partitioned records among
        them: an external key lives in the scope of its tenant."""
        self._found: WeakKeyDictionary[type, TenantDiscriminator | None] = WeakKeyDictionary()

    def discriminate(
        self, aggregate: type, discriminator: TenantDiscriminator | None
    ) -> None:
        """Declares how `aggregate` is partitioned — `None` leaves it out of tenancy, a shared
        catalog read by every tenant."""
        self.declared[aggregate] = discriminator
        self._found = WeakKeyDictionary()

    def discriminator_of(self, aggregate: type) -> TenantDiscriminator | None:
        """How `aggregate` is partitioned: what was declared for it or a class it inherits, else
        the convention when it has the field, else nothing."""
        if aggregate in self._found:
            return self._found[aggregate]
        found = next(
            (self.declared[one] for one in aggregate.__mro__ if one in self.declared),
            self.convention if declares(aggregate, self.convention.field) else None,
        )
        try:
            self._found[aggregate] = found
        except TypeError:
            pass
        return found

    def criteria(self, aggregate: type) -> Criteria | None:
        """`field IN selected` — or the project's rule — for an aggregate partitioned by tenant.

        1. Not partitioned: nothing added.
        2. No tenant named in the execution: `TenantRequired`; reading every tenant is asked by
           name.
        3. A rule of the project's own: its criteria.
        Final: the selected tenants, and the empty ones too when an empty tenant is shared.
        """
        discriminator = self.discriminator_of(aggregate)
        if discriminator is None:
            return None
        tenants = TenantContext.of(use_context())
        if tenants.is_empty:
            raise TenantRequired(
                f"{aggregate.__name__} is partitioned by tenant and this execution names none "
                "— name one (tenant_id), or read across tenants with without_tenant"
            )
        if discriminator.criteria is not None:
            return discriminator.criteria(tenants)
        selected = Condition(
            field=discriminator.field, operator=Operator.IN, value=list(tenants.selected)
        )
        if discriminator.shared is Shared.NEVER:
            return Criteria(selection=selected)
        empty = Condition(field=discriminator.field, operator=Operator.IS_NULL, value=True)
        return Criteria(selection=Any_(any=[selected, empty]))

    def stamp(self, record: Any) -> None:
        """A new record with no tenant takes the active one.

        1. Not partitioned, or its tenant already set: left as it is.
        2. An active tenant: it is the record's.
        Final: tenants selected and none active — `TenantRequired`, unless an empty tenant is
        shared: then the record is left empty, everyone's.
        """
        discriminator = self.discriminator_of(type(record))
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
