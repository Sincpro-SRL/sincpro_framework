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
from sincpro_framework.ddd.criteria import Condition, Criteria, Operator
from sincpro_framework.ddd.repositories.data_filters import DataFilters
from sincpro_framework.tenancy import (
    InMemoryMemberships,
    InMemoryTenantCatalog,
    Shared,
    Tenancy,
    Tenant,
    TenantRequired,
    TenantStatus,
    WhenMissing,
)
from tests.data_layer.orm.engines import ENGINES, fresh


@dataclass
class Account(Entity):
    code: str = ""
    tenant_id: str | None = None


@dataclass
class Plan(Entity):
    name: str = ""
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
        Plan: entity_table(
            "ten_plan",
            accounts.metadata,
            Column("name", Text, nullable=False, unique=True),
            *tenant_columns(),
        ),
    },
)
tenancy_tables = MetaData()
tenants_table = tenant_table("ten_tenant", tenancy_tables)
memberships_table = membership_table("ten_membership", tenancy_tables)


class CommandOpen(DataTransferObject):
    code: str


class CommandOpenTwice(DataTransferObject):
    code: str


tenancy = Tenancy()
tenancy.discriminator(Account)
tenancy.discriminator(Plan, shared=Shared.WHEN_EMPTY)


@pytest.fixture(params=ENGINES, ids=lambda url: url.split(":")[0].split("+")[0])
def repository(request) -> Repository:
    return Repository(fresh(request.param, accounts.metadata))  # the database knows no tenant


def test_every_door_of_the_database_reads_only_the_selected_tenants(repository):
    with tenancy.with_tenant("acme"), tenancy.isolated():
        repository.save([Account(code="1001"), Account(code="1002")])
    with tenancy.with_tenant("globex"), tenancy.isolated():
        repository.save(Account(code="1001"))  # the same code, another tenant

    with tenancy.with_tenant("acme"), tenancy.isolated():
        assert repository.count(Account, Criteria()).value == 2
        with repository.context() as unit:
            assert unit.session.scalar(select(func.count()).select_from(Account)) == 2
    with tenancy.with_tenants(["acme", "globex"]), tenancy.isolated():
        assert repository.count(Account, Criteria()).value == 3
    assert repository.count(Account, Criteria()).value == 3  # nothing isolated: as it is
    with pytest.raises(TenantRequired):
        with tenancy.isolated():
            pass


def test_a_value_unique_per_tenant_is_refused_twice_in_one(repository):
    with tenancy.with_tenant("acme"), tenancy.isolated():
        repository.save(Account(code="1001"))
        with pytest.raises(Exception):
            repository.save(Account(code="1001"))


def test_a_feature_joined_to_another_s_unit_of_work_reads_through_the_filter(repository):
    bus = UseFramework("tenancy-sql", log_after_execution=False)
    bus.add_dependency("accounts", repository)
    tenancy.on(bus)

    @bus.feature(CommandOpen)
    class Open(Feature):
        def execute(self, dto: CommandOpen) -> None:
            self.accounts.save(Account(code=dto.code))

    @bus.feature(CommandOpenTwice)
    @tenancy.isolated
    class OpenTwice(Feature):
        def execute(self, dto: CommandOpenTwice) -> None:
            with self.accounts.context():
                bus(CommandOpen(code=dto.code))
                bus(CommandOpen(code=dto.code + "-b"))

    with bus.context({"tenant_id": "acme"}):
        bus(CommandOpenTwice(code="2001"))  # Open declares nothing: it inherits the isolation
    with tenancy.with_tenant("globex"), tenancy.isolated():
        assert repository.count(Account, Criteria()).value == 0
    found = repository.fetch_all(Account).items
    assert {one.tenant_id for one in found} == {"acme"}


def test_external_keys_of_two_tenants_never_collide(repository):
    named = Criteria(selection=Condition(field="key", value="res_partner_42"))
    for tenant, entity_id in (("acme", "a-1"), ("globex", "g-1")):
        with tenancy.with_tenant(tenant), tenancy.isolated():
            repository.save(
                TenancyExternalKey(
                    origin="odoo",
                    entity_type="Partner",
                    entity_id=entity_id,
                    key="res_partner_42",
                )
            )
    with tenancy.with_tenant("acme"), tenancy.isolated():
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


def test_a_shared_row_edited_inside_a_tenant_stays_everyone_s(repository):
    with DataFilters.disabled("tenant"):
        repository.save(Plan(name="basic"))
    with tenancy.with_tenant("acme"), tenancy.isolated():
        plan = repository.fetch_all(Plan).items[0]
        plan.name = "basic+"
        repository.save(plan)
    assert [(one.name, one.tenant_id) for one in repository.fetch_all(Plan).items] == [
        ("basic+", None)
    ]


def test_shared_external_keys_are_found_without_a_tenant(repository):
    """`WhenMissing.SHARED` reads what belongs to no tenant as each discriminator spells it — an
    external key's empty scope is `""`, a column never NULL."""
    named = Criteria(selection=Condition(field="key", value="res_country_bo"))
    with tenancy.isolated(when_missing=WhenMissing.SHARED):
        repository.save(
            TenancyExternalKey(
                origin="odoo", entity_type="Country", entity_id="c-1", key="res_country_bo"
            )
        )
        found = repository.fetch_all(TenancyExternalKey, named).items
    assert [(one.entity_id, one.scope) for one in found] == [("c-1", "")]


def test_a_repository_view_keeps_what_its_calls_answer_isolated(repository):
    with tenancy.with_tenant("acme"), tenancy.isolated():
        repository.save(Account(code="3001"))
    with tenancy.with_tenant("globex"), tenancy.isolated():
        repository.save(Account(code="3002"))
    view = tenancy.repository(repository)
    with tenancy.with_tenant("acme"):
        narrowed = view.narrowed(
            Criteria(selection=Condition(field="code", operator=Operator.NE, value=""))
        )
        assert [one.code for one in narrowed.fetch_all(Account).items] == ["3001"]
        streamed = [one.code for page in view.stream(Account) for one in page.items]
        assert streamed == ["3001"]


@pytest.mark.parametrize("engine_url", ENGINES)
def test_a_status_change_keeps_when_the_tenant_was_created(engine_url):
    database = fresh(engine_url, tenancy_tables)
    catalog = DatabaseTenantCatalog(database, tenants_table)
    catalog.put(Tenant("acme"))
    with database.session() as session:
        created = session.scalar(select(tenants_table.c.created_at))
    catalog.put(Tenant("acme", status=TenantStatus.SUSPENDED))
    with database.session() as session:
        assert session.scalar(select(tenants_table.c.created_at)) == created
    assert catalog.ids(TenantStatus.SUSPENDED) == ["acme"]


def test_an_upsert_never_takes_a_key_another_tenant_holds(repository):
    """`code` is unique per tenant here, so the key is (tenant, code); on a key unique across
    tenants the conflict is bounded by the isolation: another tenant's row is skipped."""
    with tenancy.with_tenant("globex"), tenancy.isolated():
        repository.save(Plan(name="gold"))
    with tenancy.with_tenant("acme"), tenancy.isolated():
        done = repository.upsert([Plan(name="gold")], on=["name"])
    assert (done.written, done.skipped) == (0, 1)
    assert [(one.name, one.tenant_id) for one in repository.fetch_all(Plan).items] == [
        ("gold", "globex")
    ]


def test_the_body_of_a_loop_over_an_isolated_stream_is_not_isolated(repository):
    with tenancy.with_tenant("acme"), tenancy.isolated():
        repository.save(Account(code="4001"))
    with tenancy.with_tenant("globex"), tenancy.isolated():
        repository.save(Account(code="4002"))
    view = tenancy.repository(repository)
    with tenancy.with_tenant("acme"):
        for page in view.stream(Account):
            assert [one.code for one in page.items] == ["4001"]
            assert repository.count(Account, Criteria()).value == 2  # outside the view


def test_a_name_is_never_two_records_inside_a_tenant(repository):
    """A key imported before tenancy (`scope=""`) and the tenant's own of the same name: inside
    the tenant only its own is read, so an import never finds two identities for one name."""
    named = Criteria(selection=Condition(field="key", value="res_partner_42"))
    with tenancy.isolated(when_missing=WhenMissing.SHARED):
        repository.save(
            TenancyExternalKey(
                origin="odoo", entity_type="Partner", entity_id="old", key="res_partner_42"
            )
        )
    with tenancy.with_tenant("acme"), tenancy.isolated():
        repository.save(
            TenancyExternalKey(
                origin="odoo", entity_type="Partner", entity_id="new", key="res_partner_42"
            )
        )
        found = repository.fetch_all(TenancyExternalKey, named).items
    assert [one.entity_id for one in found] == ["new"]
