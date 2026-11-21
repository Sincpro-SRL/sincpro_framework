"""Roles an administrator defines at runtime, beside the ones the code declares — for every
tenant (the platform's) or for one tenant (that tenant's): granting only what the code checks,
never redefining a role of the code, checked by the catalog's own rules, bounded by what whoever
defines them holds, current on every replica within a second, published — and, when one no
longer builds or the store does not answer, never granting more than before."""

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest

from sincpro_framework.auth import (
    AdministrationBounds,
    Identity,
    InvalidGrant,
    Permission,
    PermissionDenied,
    Unauthenticated,
    security,
)
from sincpro_framework.auth.grants import (
    DefinedRoles,
    GrantAdministration,
    GrantCatalog,
    GrantResolver,
    InMemoryGrants,
    InMemoryRoleDefinitions,
    KeyValueRoleDefinitions,
    LiveCatalog,
    Privilege,
    Role,
    RoleAssigned,
    RoleChanged,
    RoleDefined,
    RoleDefinition,
    RoleRemoved,
)
from sincpro_framework.data_layer.store import InMemoryKeyValue, StoreUnavailable


class P(Permission):
    READ = "billing.invoice.read"
    ISSUE = "billing.invoice.issue"
    EXPORT = "billing.invoice.export"


invoicing = Privilege("invoicing", "Invoicing")
clerk = Role("billing.clerk", "Clerk", grants={P.READ}, privilege=invoicing, sequence=10)
manager = Role(
    "billing.manager",
    "Manager",
    grants={P.ISSUE},
    implies=(clerk,),
    privilege=invoicing,
    sequence=20,
)
catalog = GrantCatalog([manager])
AUDITOR = RoleDefinition(
    key="billing.auditor",
    name="Auditor",
    grants=frozenset({P.EXPORT}),
    implies=frozenset({"billing.clerk"}),
)


@pytest.fixture
def as_the_platform() -> Iterator[None]:
    with security.as_system("the platform administers"):
        yield


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class Recorder:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def publish(self, event: Any, /) -> None:
        self.events.append(event)


def live(definitions: Any = None, clock: Clock | None = None) -> LiveCatalog:
    return LiveCatalog(
        catalog,
        definitions or InMemoryRoleDefinitions(),
        permissions=P,
        clock=clock or Clock(),
    )


@pytest.mark.usefixtures("as_the_platform")
def test_a_role_defined_for_every_tenant_grants_what_it_says_and_what_it_implies() -> None:
    roles, grants, audit = live(), InMemoryGrants(), Recorder()
    admin = GrantAdministration(roles, grants, publisher=audit)
    admin.define_role(AUDITOR)
    admin.assign("user:ana", "bo", "billing.auditor")
    ana = GrantResolver(roles, grants).resolve(Identity.user("user:ana", tenant="bo"))
    assert ana.permissions == {P.EXPORT, P.READ}
    assert ana.roles == {"billing.auditor", "billing.clerk"}
    defined = audit.events[0]
    assert isinstance(defined, RoleDefined) and defined.tenant is None
    assert (defined.entity_type, defined.entity_id, defined.grants, defined.implies) == (
        "auth.role",
        "billing.auditor",
        ["billing.invoice.export"],
        ["billing.clerk"],
    )


@pytest.mark.usefixtures("as_the_platform")
def test_a_tenants_role_exists_for_that_tenant_alone() -> None:
    roles, grants = live(), InMemoryGrants()
    admin = GrantAdministration(roles, grants)
    admin.define_role(
        RoleDefinition(key="cash.supervisor", tenant="bo", grants=frozenset({P.READ}))
    )
    assert "cash.supervisor" in roles.current("bo").roles
    assert "cash.supervisor" not in roles.current("pe").roles
    assert "cash.supervisor" not in roles.current().roles
    admin.assign("user:ana", "bo", "cash.supervisor")
    with pytest.raises(InvalidGrant, match="is not a role"):
        admin.assign("user:bea", "pe", "cash.supervisor")
    admin.define_role(AUDITOR)  # every tenant's
    with pytest.raises(InvalidGrant, match="every tenant shares"):
        admin.define_role(RoleDefinition(key="billing.auditor", tenant="bo"))


@pytest.mark.usefixtures("as_the_platform")
def test_what_is_defined_only_adds_to_the_code() -> None:
    admin = GrantAdministration(live(), InMemoryGrants())
    with pytest.raises(InvalidGrant, match="declared in code"):
        admin.define_role(RoleDefinition(key="billing.clerk", grants=frozenset({P.EXPORT})))
    with pytest.raises(InvalidGrant, match="which no code declares"):
        admin.define_role(
            RoleDefinition(key="billing.root", grants=frozenset({"billing.everything"}))
        )
    with pytest.raises(InvalidGrant, match="is not a role"):
        admin.define_role(
            RoleDefinition(key="billing.x", implies=frozenset({"billing.ghost"}))
        )
    with pytest.raises(InvalidGrant, match="is not a privilege"):
        admin.define_role(RoleDefinition(key="billing.y", privilege="payroll"))
    with pytest.raises(InvalidGrant, match="does not imply"):  # a level above the manager
        admin.define_role(
            RoleDefinition(key="billing.boss", privilege="invoicing", sequence=30)
        )
    with pytest.raises(InvalidGrant, match="declared in code"):
        admin.remove_role("billing.manager")


@pytest.mark.usefixtures("as_the_platform")
def test_a_change_is_published_and_a_removed_role_grants_nothing() -> None:
    roles, grants, audit = live(), InMemoryGrants(), Recorder()
    admin = GrantAdministration(roles, grants, publisher=audit)
    admin.define_role(AUDITOR)
    admin.assign("user:ana", "bo", "billing.auditor")
    admin.define_role(AUDITOR.model_copy(update={"implies": frozenset()}))
    resolver = GrantResolver(roles, grants)
    assert resolver.resolve(Identity.user("user:ana", tenant="bo")).permissions == {P.EXPORT}
    admin.remove_role("billing.auditor")
    assert resolver.resolve(Identity.user("user:ana", tenant="bo")).permissions == set()
    with pytest.raises(InvalidGrant, match="is not a role"):
        admin.assign("user:bea", "bo", "billing.auditor")
    assert [type(one) for one in audit.events] == [
        RoleDefined,
        RoleAssigned,
        RoleChanged,
        RoleRemoved,
    ]


@pytest.mark.usefixtures("as_the_platform")
def test_a_role_another_one_implies_is_not_removed() -> None:
    admin = GrantAdministration(live(), InMemoryGrants())
    admin.define_role(AUDITOR)
    admin.define_role(
        RoleDefinition(key="billing.lead", implies=frozenset({"billing.auditor"}))
    )
    with pytest.raises(InvalidGrant, match="implied by billing.lead"):
        admin.remove_role("billing.auditor")


BO_ADMIN = Identity.user("user:root-bo", tenant="bo", permissions={P.READ, P.ISSUE})
"""The administrator of tenant bo: reads and issues — never exports."""


def test_a_tenants_administrator_gives_only_what_it_holds_and_only_in_its_tenant() -> None:
    roles, grants = live(), InMemoryGrants()
    admin = GrantAdministration(roles, grants)
    with security.as_identity(BO_ADMIN):
        admin.define_role(
            RoleDefinition(
                key="cash.supervisor", tenant="bo", implies=frozenset({"billing.clerk"})
            )
        )
        admin.assign("user:ana", "bo", "cash.supervisor")
        admin.assign("user:ana", "bo", "billing.manager")
        with pytest.raises(
            PermissionDenied, match="billing.invoice.export, which user:root-bo"
        ):
            admin.define_role(
                RoleDefinition(key="cash.exporter", tenant="bo", grants=frozenset({P.EXPORT}))
            )
        with pytest.raises(PermissionDenied, match="in tenant pe, not bo"):
            admin.assign("user:bea", "pe", "billing.clerk")
        with pytest.raises(PermissionDenied, match="every tenant"):
            admin.define_role(
                RoleDefinition(key="billing.viewer", grants=frozenset({P.READ}))
            )
        with pytest.raises(PermissionDenied, match="every tenant"):
            admin.entitle("bo", "payments")
    with security.as_system("the platform"):
        admin.define_role(AUDITOR)
    with security.as_identity(BO_ADMIN):
        with pytest.raises(PermissionDenied, match="assigning billing.auditor gives"):
            admin.assign("user:ana", "bo", "billing.auditor")  # it would give EXPORT
    with pytest.raises(Unauthenticated):
        admin.assign("user:ana", "bo", "billing.clerk")  # nobody known administers nothing


def test_a_superadmin_and_unenforced_bounds_are_not_bounded() -> None:
    roles = live()
    superadmin = Identity.user("user:ops", permissions={"platform.grants.manage"})
    admin = GrantAdministration(
        roles,
        InMemoryGrants(),
        bounds=AdministrationBounds(superadmin="platform.grants.manage"),
    )
    with security.as_identity(superadmin):
        admin.define_role(AUDITOR)
        admin.assign("user:ana", "pe", "billing.auditor")
    trusting = GrantAdministration(
        roles, InMemoryGrants(), bounds=AdministrationBounds(False)
    )
    with security.as_identity(Identity.user("user:anyone")):
        trusting.assign("user:ana", "pe", "billing.manager")


@pytest.mark.usefixtures("as_the_platform")
def test_another_replica_holds_it_once_it_checks_again() -> None:
    store, clock = InMemoryKeyValue(), Clock()
    here = live(KeyValueRoleDefinitions(store), clock)
    there = live(KeyValueRoleDefinitions(store), clock)
    assert "billing.auditor" not in there.current("bo").roles
    GrantAdministration(here, InMemoryGrants()).define_role(AUDITOR)
    assert "billing.auditor" in here.current("bo").roles  # the writer at once
    assert "billing.auditor" not in there.current("bo").roles  # within check_every
    clock.now += 1.0
    assert "billing.auditor" in there.current("bo").roles


@pytest.mark.usefixtures("as_the_platform")
def test_two_administrators_writing_one_version_never_lose_a_role() -> None:
    store = KeyValueRoleDefinitions(InMemoryKeyValue())
    assert store.write("bo", 0, [AUDITOR.model_copy(update={"tenant": "bo"})])
    assert not store.write("bo", 0, [])  # over a version gone
    first = GrantAdministration(live(store), InMemoryGrants())
    second = GrantAdministration(live(store), InMemoryGrants())
    first.define_role(
        RoleDefinition(key="cash.lead", tenant="bo", grants=frozenset({P.READ}))
    )
    second.define_role(
        RoleDefinition(key="cash.viewer", tenant="bo", grants=frozenset({P.READ}))
    )
    assert {one.key for one in store.read("bo").roles} == {
        "billing.auditor",
        "cash.lead",
        "cash.viewer",
    }


def test_a_role_that_no_longer_builds_grants_nothing_and_the_others_still_do() -> None:
    definitions = InMemoryRoleDefinitions()
    definitions.write(
        None,
        0,
        [
            AUDITOR,
            RoleDefinition(key="billing.x", implies=frozenset({"billing.gone"})),
            RoleDefinition(key="billing.y", implies=frozenset({"billing.x"})),
        ],
    )
    roles = live(definitions)
    assert "billing.auditor" in roles.current().roles
    assert set(roles.problems()) == {"billing.x", "billing.y"}
    assert "billing.gone is not a role" in roles.problems()["billing.x"]


class Unavailable(InMemoryRoleDefinitions):
    def __init__(self) -> None:
        super().__init__()
        self.down = False

    def read(self, tenant: str | None = None) -> DefinedRoles:
        if self.down:
            raise StoreUnavailable("redis is down")
        return super().read(tenant)


@pytest.mark.usefixtures("as_the_platform")
def test_a_store_that_does_not_answer_leaves_the_last_catalog_read() -> None:
    definitions, clock = Unavailable(), Clock()
    roles = live(definitions, clock)
    GrantAdministration(roles, InMemoryGrants()).define_role(AUDITOR)
    assert "billing.auditor" in roles.current().roles
    definitions.down = True
    clock.now += timedelta(minutes=5).total_seconds()
    assert "billing.auditor" in roles.current().roles


@pytest.mark.usefixtures("as_the_platform")
def test_a_catalog_of_code_alone_defines_nothing_at_runtime() -> None:
    with pytest.raises(Exception, match="on a LiveCatalog"):
        GrantAdministration(catalog, InMemoryGrants()).define_role(AUDITOR)
