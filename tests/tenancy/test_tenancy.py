"""Tenancy on a bus, end to end: the tenant named by the context, what a handler reads and
writes through a store the tenant filter isolates, the memberships that validate the caller,
and the use cases that work across tenants or once per tenant.

No base class is required of an aggregate: `Invoice` inherits the recipe, `Order` declares the
field by hand, and `Branch` is partitioned by a field of another name.
"""

from dataclasses import dataclass
from decimal import Decimal

import pytest

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import Entity
from sincpro_framework.ddd.criteria import Criteria
from sincpro_framework.tenancy import (
    InMemoryMemberships,
    InMemoryTenantCatalog,
    Shared,
    Tenancy,
    Tenant,
    TenantNotAllowed,
    TenantNotChosen,
    TenantOwnedMixin,
    TenantRequired,
    TenantStatus,
)


@dataclass
class Invoice(TenantOwnedMixin, Entity):
    total: Decimal = Decimal("0")


@dataclass
class Order(Entity):
    tenant_id: str | None = None
    code: str = ""


@dataclass
class Branch(Entity):
    company_id: str | None = None
    name: str = ""


@dataclass
class Currency(Entity):
    tenant_id: str | None = None
    code: str = ""


class CommandIssue(DataTransferObject):
    total: Decimal


class CommandCount(DataTransferObject):
    pass


class ResponseCount(DataTransferObject):
    value: int


class CommandEveryone(DataTransferObject):
    pass


class CommandClose(DataTransferObject):
    pass


class CommandRemind(DataTransferObject):
    pass


reminded: list[str] = []


def _billing(tenancy: Tenancy, store: MemoryRepository) -> UseFramework:
    bus = UseFramework("tenancy-billing", log_after_execution=False)
    bus.add_dependency("store", store)
    tenancy.on(bus)

    @bus.feature(CommandIssue)
    class Issue(Feature):
        def execute(self, dto: CommandIssue) -> Invoice:
            invoice = Invoice(total=dto.total)
            self.store.save(invoice)
            return invoice

    @bus.feature(CommandCount)
    class Count(Feature):
        def execute(self, dto: CommandCount) -> ResponseCount:
            return ResponseCount(value=self.store.count(Invoice, Criteria()).value)

    @bus.app_service(CommandEveryone)
    @tenancy.without_tenant(reason="consolidated report")
    class Everyone(ApplicationService):
        def execute(self, dto: CommandEveryone) -> ResponseCount:
            return self.feature_bus.execute(CommandCount(), ResponseCount)

    @bus.feature(CommandClose)
    @tenancy.required
    class Close(Feature):
        def execute(self, dto: CommandClose) -> str:
            return self.tenancy.active

    @bus.app_service(CommandRemind)
    @tenancy.each_tenant
    class Remind(ApplicationService):
        def execute(self, dto: CommandRemind) -> None:
            reminded.append(self.tenancy.active)

    return bus


@pytest.fixture
def store() -> MemoryRepository:
    return MemoryRepository()


@pytest.fixture
def tenancy() -> Tenancy:
    return Tenancy(catalog=InMemoryTenantCatalog.of("acme", "globex"))


@pytest.fixture
def billing(tenancy, store) -> UseFramework:
    tenancy.isolate(store)
    return _billing(tenancy, store)


def test_a_write_is_stamped_with_the_tenant_and_reads_see_only_it(billing):
    with billing.context({"tenant_id": "acme"}):
        issued = billing(CommandIssue(total=Decimal(10)))
        billing(CommandIssue(total=Decimal(20)))
    with billing.context({"tenant_id": "globex"}):
        billing(CommandIssue(total=Decimal(30)))
        assert billing(CommandCount()).value == 1
    with billing.context({"tenant_id": "acme"}):
        assert billing(CommandCount()).value == 2
    assert issued.tenant_id == "acme"


def test_reading_partitioned_data_with_no_tenant_is_refused(billing):
    with pytest.raises(TenantRequired):
        billing(CommandCount())


def test_a_selection_reads_several_tenants(billing):
    for tenant in ("acme", "globex"):
        with billing.context({"tenant_id": tenant}):
            billing(CommandIssue(total=Decimal(1)))
    with billing.context({"tenant_ids": ["acme", "globex"]}):
        assert billing(CommandCount()).value == 2


def test_without_tenant_reads_every_tenant_by_name(billing):
    for tenant in ("acme", "globex"):
        with billing.context({"tenant_id": tenant}):
            billing(CommandIssue(total=Decimal(1)))
    assert billing(CommandEveryone()).value == 2


def test_a_use_case_that_requires_a_tenant_refuses_before_it_runs(billing):
    with pytest.raises(TenantRequired):
        billing(CommandClose())
    with billing.context({"tenant_id": "acme"}):
        assert billing(CommandClose()) == "acme"


def test_each_tenant_runs_once_per_active_tenant_of_the_catalog(billing, tenancy):
    reminded.clear()
    billing(CommandRemind())
    assert reminded == ["acme", "globex"]
    assert tenancy.each(lambda: tenancy.active) == {"acme": "acme", "globex": "globex"}


def test_memberships_validate_what_the_caller_names(store):
    tenancy = Tenancy(
        memberships=InMemoryMemberships({"ana": ["acme"], "bob": ["acme", "globex"]})
    )
    tenancy.isolate(store)
    bus = _billing(tenancy, store)

    with bus.context({"user_id": "ana", "tenant_id": "globex"}):
        with pytest.raises(TenantNotAllowed):
            bus(CommandCount())
    with bus.context({"user_id": "ana"}):
        assert bus(CommandClose()) == "acme"  # her only tenant, chosen for her
    with bus.context({"user_id": "bob"}):
        with pytest.raises(TenantNotChosen):
            bus(CommandClose())


def test_a_tenant_the_catalog_holds_suspended_is_never_served(store):
    catalog = InMemoryTenantCatalog(
        Tenant("acme"), Tenant("globex", status=TenantStatus.SUSPENDED)
    )
    tenancy = Tenancy(catalog=catalog)
    bus = _billing(tenancy.isolate(store) and tenancy, store)
    with bus.context({"tenant_id": "globex"}):
        with pytest.raises(TenantNotAllowed, match="suspended"):
            bus(CommandClose())


def test_no_base_class_is_required_and_another_field_is_declared(store):
    tenancy = Tenancy()
    tenancy.isolate(store)
    tenancy.discriminator(Branch, field="company_id")
    with tenancy.with_tenant("acme"):
        store.save(Order(code="O-1"))
        store.save(Branch(name="centro"))
    with tenancy.with_tenant("globex"):
        assert store.count(Order, Criteria()).value == 0
        assert store.count(Branch, Criteria()).value == 0
    with tenancy.with_tenant("acme"):
        assert store.get(Order, store.fetch_all(Order).items[0].id).tenant_id == "acme"
        assert store.fetch_all(Branch).items[0].company_id == "acme"


def test_a_shared_catalog_and_shared_empty_rows(store):
    tenancy = Tenancy(shared=Shared.WHEN_EMPTY)
    tenancy.isolate(store)
    tenancy.shared(Currency)
    store.add(Order(code="everyone"))  # no tenant: shared
    with tenancy.with_tenant("acme"):
        store.save(Order(code="acme only"))
        store.save(Currency(code="BOB"))
    with tenancy.with_tenant("globex"):
        assert [one.code for one in store.fetch_all(Order).items] == ["everyone"]
        assert store.count(Currency, Criteria()).value == 1


def test_the_sub_criteria_of_a_read_of_the_project_s_own(store):
    tenancy = Tenancy()
    with tenancy.with_tenants(["acme", "globex"]):
        criteria = tenancy.criteria(Invoice)
    assert criteria is not None and criteria.expression is not None
    assert criteria.expression.model_dump()["value"] == ["acme", "globex"]
