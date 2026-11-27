"""Tenancy on a real engine: tables declared with the recipes, every door of the database
narrowed to the selected tenants — a repository, a Feature joined to another's unit of work, a
plain session —, unique values per tenant, external keys that never collide across tenants, and
the catalog and memberships stored in their own tables.
"""

from dataclasses import dataclass

import pytest
from sqlalchemy import Column, MetaData, Text, func, select
from sqlalchemy.orm import registry

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.data_layer.orm import (
    DatabaseMemberships,
    DatabaseTenantCatalog,
    map_aggregates,
)
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.repository import Repository
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table import (
    entity_table,
    external_key_table,
    membership_table,
    tenant_columns,
    tenant_table,
    unique_per_tenant,
)
from sincpro_framework.ddd import Entity, ExternalKey
from sincpro_framework.ddd.criteria import Condition, Criteria
from sincpro_framework.tenancy import (
    InMemoryMemberships,
    InMemoryTenantCatalog,
    Tenancy,
    Tenant,
    TenantRequired,
    TenantStatus,
)
from tests.data_layer.orm.engines import ENGINES, fresh


@dataclass
class Account(Entity):
    code: str = ""
    tenant_id: str | None = None


class TenancyExternalKey(ExternalKey):
    """The external keys of this suite's registry."""


accounts = registry(metadata=MetaData())
map_aggregates(
    accounts,
    {
        Account: entity_table(
            "ten_account",
            accounts.metadata,
            Column("code", Text, nullable=False),
            *tenant_columns(),
            unique_per_tenant("code"),
        ),
        TenancyExternalKey: external_key_table("ten_external_keys", accounts.metadata),
    },
)
tenancy_tables = MetaData()
tenants_table = tenant_table("ten_tenant", tenancy_tables)
memberships_table = membership_table("ten_membership", tenancy_tables)


class CommandOpen(DataTransferObject):
    code: str


class CommandOpenTwice(DataTransferObject):
    code: str


@pytest.fixture(params=ENGINES, ids=lambda url: url.split(":")[0].split("+")[0])
def repository(request) -> Repository:
    tenancy = Tenancy()
    database = tenancy.isolate(fresh(request.param, accounts.metadata))
    return Repository(database)


def test_every_door_of_the_database_reads_only_the_selected_tenants(repository):
    tenancy = Tenancy()
    with tenancy.with_tenant("acme"):
        repository.save([Account(code="1001"), Account(code="1002")])
    with tenancy.with_tenant("globex"):
        repository.save(Account(code="1001"))  # the same code, another tenant

    with tenancy.with_tenant("acme"):
        assert repository.count(Account, Criteria()).value == 2
        with repository.context() as unit:
            assert unit.session.scalar(select(func.count()).select_from(Account)) == 2
    with tenancy.with_tenants(["acme", "globex"]):
        assert repository.count(Account, Criteria()).value == 3
    with pytest.raises(TenantRequired):
        repository.count(Account, Criteria())


def test_a_value_unique_per_tenant_is_refused_twice_in_one(repository):
    tenancy = Tenancy()
    with tenancy.with_tenant("acme"):
        repository.save(Account(code="1001"))
        with pytest.raises(Exception):
            repository.save(Account(code="1001"))


def test_a_feature_joined_to_another_s_unit_of_work_reads_through_the_filter(repository):
    tenancy = Tenancy()
    bus = UseFramework("tenancy-sql", log_after_execution=False)
    bus.add_dependency("accounts", repository)
    tenancy.on(bus)

    @bus.feature(CommandOpen)
    class Open(Feature):
        def execute(self, dto: CommandOpen) -> None:
            self.accounts.save(Account(code=dto.code))

    @bus.feature(CommandOpenTwice)
    class OpenTwice(Feature):
        def execute(self, dto: CommandOpenTwice) -> None:
            with self.accounts.context():
                bus(CommandOpen(code=dto.code))
                bus(CommandOpen(code=dto.code + "-b"))

    with bus.context({"tenant_id": "acme"}):
        bus(CommandOpenTwice(code="2001"))
    with bus.context({"tenant_id": "globex"}):
        assert repository.count(Account, Criteria()).value == 0
    with bus.context({"tenant_id": "acme"}):
        found = repository.fetch_all(Account).items
    assert {one.tenant_id for one in found} == {"acme"}


def test_external_keys_of_two_tenants_never_collide(repository):
    tenancy = Tenancy()
    named = Criteria(selection=Condition(field="key", value="res_partner_42"))
    for tenant, entity_id in (("acme", "a-1"), ("globex", "g-1")):
        with tenancy.with_tenant(tenant):
            repository.save(
                TenancyExternalKey(
                    origin="odoo",
                    entity_type="Partner",
                    entity_id=entity_id,
                    key="res_partner_42",
                )
            )
    with tenancy.with_tenant("acme"):
        found = repository.fetch_all(TenancyExternalKey, named).items
    assert [(one.entity_id, one.scope) for one in found] == [("a-1", "acme")]


@pytest.mark.parametrize("engine_url", ENGINES)
def test_the_catalog_and_memberships_on_a_database_behave_as_in_memory(engine_url):
    database = fresh(engine_url, tenancy_tables)
    on_disk = (
        DatabaseTenantCatalog(database, tenants_table),
        DatabaseMemberships(database, memberships_table),
    )
    in_memory = (InMemoryTenantCatalog(), InMemoryMemberships())
    for catalog, memberships in (on_disk, in_memory):
        catalog.put(Tenant("acme", "Acme"))
        catalog.put(Tenant("globex", status=TenantStatus.SUSPENDED))
        memberships.join("ana", "acme", "admin")
        memberships.join("ana", "globex")
        memberships.leave("ana", "globex")

        assert catalog.ids() == ["acme"]
        suspended = catalog.get("globex")
        assert suspended is not None and suspended.status is TenantStatus.SUSPENDED
        assert catalog.get("nobody") is None
        assert {key: one.role for key, one in memberships.tenants_of("ana").items()} == {
            "acme": "admin"
        }
        assert memberships.members_of("acme") == {"ana": "admin"}
