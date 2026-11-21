"""Roles an administrator defines at runtime, beside the ones the code declares — for every
tenant (the platform's) or for one tenant (that tenant's) — and who may give, define, change or
take them away: granting only what the code checks, checked inside the write against what is
stored now, never reaching another tenant, never coming back to life, never handed by an
administrator who does not hold them, current on every replica within a second, and published.
"""

import threading
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest

from sincpro_framework.auth import (
    BoundedAdministration,
    Identity,
    InvalidGrant,
    Permission,
    PermissionDenied,
    TrustedAdministration,
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
    RoleAdministration,
    RoleDefined,
    RoleDefinition,
    RoleRemoved,
    RoleUnassigned,
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
admin_role = Role("billing.admin", "Administrator")  # grants nothing: `auth.role` asks for it
portal = Role("portal", "Portal", disjoint={"billing.clerk"})
catalog = GrantCatalog([manager, admin_role, portal])
AUDITOR = RoleDefinition(
    key="billing.auditor",
    name="Auditor",
    grants=frozenset({P.EXPORT}),
    implies=frozenset({"billing.clerk"}),
)
BO_ADMIN = Identity.user(
    "user:root-bo",
    tenant="bo",
    roles={"billing.manager", "billing.clerk"},
    permissions={P.READ, P.ISSUE},
)
"""Tenant bo's administrator: a manager — reads and issues, never exports."""
PLATFORM = Identity.system("the platform")


@pytest.fixture
def as_the_platform() -> Iterator[None]:
    with security.as_identity(PLATFORM):
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


def as_(identity: Identity) -> Any:
    return security.as_identity(identity)


# Defining


@pytest.mark.usefixtures("as_the_platform")
def test_a_role_defined_for_every_tenant_grants_what_it_says_and_what_it_implies() -> None:
    roles, grants, audit = live(), InMemoryGrants(), Recorder()
    RoleAdministration(roles, grants, publisher=audit).define(AUDITOR)
    GrantAdministration(roles, grants).assign("user:ana", "bo", "billing.auditor")
    ana = GrantResolver(roles, grants).resolve(Identity.user("user:ana", tenant="bo"))
    assert ana.permissions == {P.EXPORT, P.READ}
    assert ana.roles == {"billing.auditor", "billing.clerk"}
    (defined,) = audit.events
    assert isinstance(defined, RoleDefined) and defined.tenant is None
    assert (defined.entity_type, defined.entity_id, defined.grants) == (
        "auth.role",
        "billing.auditor",
        ["billing.invoice.export"],
    )


@pytest.mark.usefixtures("as_the_platform")
def test_a_tenants_role_exists_for_that_tenant_alone() -> None:
    roles, grants = live(), InMemoryGrants()
    RoleAdministration(roles, grants).define(
        RoleDefinition(key="cash.supervisor", tenant="bo", grants=frozenset({P.READ}))
    )
    assert "cash.supervisor" in roles.current("bo").roles
    assert "cash.supervisor" not in roles.current("pe").roles
    assert "cash.supervisor" not in roles.current().roles
    with pytest.raises(InvalidGrant, match="is not a role"):
        GrantAdministration(roles, grants).assign("user:bea", "pe", "cash.supervisor")


@pytest.mark.usefixtures("as_the_platform")
def test_what_is_defined_only_adds_to_the_code() -> None:
    roles = RoleAdministration(live(), InMemoryGrants())
    with pytest.raises(InvalidGrant, match="declared in code"):
        roles.define(RoleDefinition(key="billing.clerk", grants=frozenset({P.EXPORT})))
    with pytest.raises(InvalidGrant, match="which no code declares"):
        roles.define(RoleDefinition(key="billing.root", grants=frozenset({"billing.all"})))
    with pytest.raises(InvalidGrant, match="is not a role"):
        roles.define(RoleDefinition(key="billing.x", implies=frozenset({"billing.ghost"})))
    with pytest.raises(InvalidGrant, match="is not a privilege"):
        roles.define(RoleDefinition(key="billing.y", privilege="payroll"))
    with pytest.raises(InvalidGrant, match="does not imply"):  # a level above the manager
        roles.define(RoleDefinition(key="billing.boss", privilege="invoicing", sequence=30))
    with pytest.raises(InvalidGrant, match="declared in code"):
        roles.remove("billing.manager")


# Who may give, define, change and take away


def test_a_tenants_administrator_defines_and_gives_only_what_it_holds() -> None:
    roles, grants = live(), InMemoryGrants()
    defining, assigning = RoleAdministration(roles, grants), GrantAdministration(
        roles, grants
    )
    with as_(BO_ADMIN):
        defining.define(
            RoleDefinition(key="cash.desk", tenant="bo", implies=frozenset({"billing.clerk"}))
        )
        assigning.assign(
            "user:ana", "bo", "cash.desk"
        )  # its tenant's own, made of what it holds
        with pytest.raises(
            PermissionDenied, match="billing.invoice.export, which user:root-bo"
        ):
            defining.define(
                RoleDefinition(key="cash.export", tenant="bo", grants=frozenset({P.EXPORT}))
            )
        with pytest.raises(PermissionDenied, match="in tenant pe, not bo"):
            assigning.assign("user:bea", "pe", "billing.clerk")
        with pytest.raises(PermissionDenied, match="every tenant"):
            defining.define(RoleDefinition(key="billing.viewer", grants=frozenset({P.READ})))
        with pytest.raises(PermissionDenied, match="every tenant"):
            assigning.entitle("bo", "payments")
    with pytest.raises(Unauthenticated):
        assigning.assign(
            "user:ana", "bo", "billing.clerk"
        )  # nobody known administers nothing


def test_a_role_that_grants_nothing_is_given_only_by_who_holds_it() -> None:
    roles, grants = live(), InMemoryGrants()
    with as_(BO_ADMIN), pytest.raises(PermissionDenied, match="needs billing.admin"):
        GrantAdministration(roles, grants).assign("user:ana", "bo", "billing.admin")
    with as_(BO_ADMIN), pytest.raises(PermissionDenied, match="needs billing.admin"):
        RoleAdministration(roles, grants).define(
            RoleDefinition(key="cash.boss", tenant="bo", implies=frozenset({"billing.admin"}))
        )


def test_nobody_administers_themselves() -> None:
    roles, grants = live(), InMemoryGrants()
    with as_(BO_ADMIN), pytest.raises(
        PermissionDenied, match="nobody administers themselves"
    ):
        GrantAdministration(roles, grants).assign("user:root-bo", "bo", "billing.manager")


def test_a_weaker_administrator_never_takes_away_what_it_could_not_give() -> None:
    roles, grants = live(), InMemoryGrants()
    with as_(PLATFORM):
        RoleAdministration(roles, grants).define(
            RoleDefinition(key="cash.owner", tenant="bo", grants=frozenset({P.EXPORT}))
        )
        GrantAdministration(roles, grants).assign("user:owner", "bo", "cash.owner")
        GrantAdministration(roles, grants).assign("user:owner", "bo", "billing.manager")
    clerk_admin = Identity.user(
        "user:clerk-admin", tenant="bo", roles={"billing.clerk"}, permissions={P.READ}
    )
    with as_(clerk_admin):
        with pytest.raises(PermissionDenied, match="cash.owner"):
            GrantAdministration(roles, grants).unassign("user:owner", "bo", "cash.owner")
        with pytest.raises(PermissionDenied, match="billing.manager"):
            GrantAdministration(roles, grants).set_level(
                "user:owner", "bo", "invoicing", None
            )
        with pytest.raises(PermissionDenied, match="cash.owner"):
            RoleAdministration(roles, grants).define(
                RoleDefinition(key="cash.owner", tenant="bo", grants=frozenset({P.READ}))
            )
        with pytest.raises(PermissionDenied, match="cash.owner"):
            RoleAdministration(roles, grants).remove("cash.owner", "bo")


def test_a_superadmin_and_a_trusting_policy_are_not_bounded() -> None:
    roles = live()
    ops = Identity.user("user:ops", permissions={"platform.grants.manage"})
    superadmin = BoundedAdministration(superadmin="platform.grants.manage")
    with as_(ops):
        RoleAdministration(roles, InMemoryGrants(), policy=superadmin).define(AUDITOR)
        GrantAdministration(roles, InMemoryGrants(), policy=superadmin).assign(
            "user:ana", "pe", "billing.auditor"
        )
    with as_(Identity.user("user:anyone")):
        GrantAdministration(roles, InMemoryGrants(), policy=TrustedAdministration()).assign(
            "user:ana", "pe", "billing.manager"
        )


# Changing a role, removing it


@pytest.mark.usefixtures("as_the_platform")
def test_an_assigned_role_keeps_its_shape_and_every_tenants_never_changes_shape() -> None:
    roles, grants = live(), InMemoryGrants()
    defining = RoleAdministration(roles, grants)
    desk = RoleDefinition(key="cash.desk", tenant="bo", grants=frozenset({P.READ}))
    defining.define(desk)
    defining.define(desk.model_copy(update={"name": "Desk"}))  # unheld: anything may change
    GrantAdministration(roles, grants).assign("user:ana", "bo", "portal")
    GrantAdministration(roles, grants).assign("user:ana", "bo", "cash.desk")
    with pytest.raises(InvalidGrant, match="is assigned"):  # ana: portal and clerk together
        defining.define(desk.model_copy(update={"implies": frozenset({"billing.clerk"})}))
    defining.define(desk.model_copy(update={"name": "Front desk"}))  # its shape kept
    defining.define(AUDITOR)
    with pytest.raises(InvalidGrant, match="is assigned"):
        defining.define(AUDITOR.model_copy(update={"implies": frozenset()}))


@pytest.mark.usefixtures("as_the_platform")
def test_a_removed_key_never_comes_back_and_a_tenants_role_takes_its_assignments() -> None:
    roles, grants, audit = live(), InMemoryGrants(), Recorder()
    defining = RoleAdministration(roles, grants, publisher=audit)
    defining.define(AUDITOR)
    defining.remove("billing.auditor")
    with pytest.raises(InvalidGrant, match="never reused"):
        defining.define(AUDITOR)
    with pytest.raises(InvalidGrant, match="never reused"):
        defining.define(AUDITOR.model_copy(update={"tenant": "bo"}))
    desk = RoleDefinition(key="cash.desk", tenant="bo", grants=frozenset({P.READ}))
    defining.define(desk)
    GrantAdministration(roles, grants).assign("user:ana", "bo", "cash.desk")
    defining.remove("cash.desk", "bo")
    assert grants.held("user:ana", "bo").roles == frozenset()
    assert [type(one) for one in audit.events] == [
        RoleDefined,
        RoleRemoved,
        RoleDefined,
        RoleUnassigned,
        RoleRemoved,
    ]


@pytest.mark.usefixtures("as_the_platform")
def test_a_role_another_one_implies_is_not_removed() -> None:
    defining = RoleAdministration(live(), InMemoryGrants())
    defining.define(AUDITOR)
    defining.define(
        RoleDefinition(key="billing.lead", implies=frozenset({"billing.auditor"}))
    )
    with pytest.raises(InvalidGrant, match="implied by billing.lead"):
        defining.remove("billing.auditor")


@pytest.mark.usefixtures("as_the_platform")
def test_a_tenants_own_role_wins_over_one_every_tenant_shares_defined_later() -> None:
    roles, grants = live(), InMemoryGrants()
    defining = RoleAdministration(roles, grants)
    defining.define(
        RoleDefinition(key="cash.viewer", tenant="bo", grants=frozenset({P.READ}))
    )
    GrantAdministration(roles, grants).assign("user:ana", "bo", "cash.viewer")
    defining.define(RoleDefinition(key="cash.viewer", grants=frozenset({P.READ, P.EXPORT})))
    ana = GrantResolver(roles, grants).resolve(Identity.user("user:ana", tenant="bo"))
    assert ana.permissions == {P.READ}  # bo's own, never what nobody assigned in bo
    assert "every tenant shares" in roles.problems("bo")["cash.viewer"]
    with pytest.raises(InvalidGrant, match="every tenant shares"):
        defining.define(RoleDefinition(key="cash.viewer", tenant="pe"))


# Stored, read, raced


def test_a_change_is_checked_against_what_is_stored_never_a_stale_catalog() -> None:
    store, clock = InMemoryKeyValue(), Clock()
    here = live(KeyValueRoleDefinitions(store), clock)
    there = live(KeyValueRoleDefinitions(store), clock)
    desk = RoleDefinition(key="cash.desk", tenant="bo", grants=frozenset({P.READ}))
    with as_(PLATFORM):
        RoleAdministration(here, InMemoryGrants()).define(desk)
    assert "cash.desk" in there.current("bo").roles  # there read it, and holds it a second
    with as_(PLATFORM):
        RoleAdministration(here, InMemoryGrants()).define(
            RoleDefinition(key="cash.export", tenant="bo", grants=frozenset({P.EXPORT}))
        )
    with as_(BO_ADMIN), pytest.raises(PermissionDenied, match="billing.invoice.export"):
        RoleAdministration(there, InMemoryGrants()).define(
            desk.model_copy(update={"implies": frozenset({"cash.export"})})
        )


@pytest.mark.usefixtures("as_the_platform")
def test_another_replica_holds_it_once_it_checks_again() -> None:
    store, clock = InMemoryKeyValue(), Clock()
    here = live(KeyValueRoleDefinitions(store), clock)
    there = live(KeyValueRoleDefinitions(store), clock)
    assert "billing.auditor" not in there.current("bo").roles
    RoleAdministration(here, InMemoryGrants()).define(AUDITOR)
    assert "billing.auditor" in here.current("bo").roles  # the writer at once
    assert "billing.auditor" not in there.current("bo").roles  # within check_every
    clock.now += 1.0
    assert "billing.auditor" in there.current("bo").roles


@pytest.mark.usefixtures("as_the_platform")
def test_two_administrators_writing_one_version_never_lose_a_role() -> None:
    store = KeyValueRoleDefinitions(InMemoryKeyValue())
    first = RoleAdministration(live(store), InMemoryGrants())
    second = RoleAdministration(live(store), InMemoryGrants())
    first.define(RoleDefinition(key="cash.lead", tenant="bo", grants=frozenset({P.READ})))
    second.define(RoleDefinition(key="cash.viewer", tenant="bo", grants=frozenset({P.READ})))
    assert {one.key for one in store.read("bo").roles} == {"cash.lead", "cash.viewer"}
    stale = store.read("bo")
    assert not store.write("bo", DefinedRoles(version=stale.version, roles=()))


def test_a_role_that_no_longer_builds_grants_nothing_and_the_others_still_do() -> None:
    definitions = InMemoryRoleDefinitions()
    definitions.write(
        None,
        DefinedRoles(
            version=1,
            roles=(
                AUDITOR,
                RoleDefinition(key="billing.x", implies=frozenset({"billing.gone"})),
                RoleDefinition(key="billing.y", implies=frozenset({"billing.x"})),
            ),
        ),
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
def test_a_store_that_does_not_answer_leaves_the_last_catalog_even_right_after_a_write() -> (
    None
):
    definitions, clock = Unavailable(), Clock()
    roles = live(definitions, clock)
    defining = RoleAdministration(roles, InMemoryGrants())
    defining.define(RoleDefinition(key="cash.desk", tenant="bo", grants=frozenset({P.READ})))
    assert "cash.desk" in roles.current("bo").roles
    defining.define(RoleDefinition(key="cash.lead", tenant="bo", grants=frozenset({P.READ})))
    definitions.down = True  # right after the write, before any read
    clock.now += timedelta(minutes=5).total_seconds()
    assert "cash.desk" in roles.current("bo").roles


class Slow(InMemoryRoleDefinitions):
    """A store whose reads for tenant bo hang until released."""

    def __init__(self) -> None:
        super().__init__()
        self.released = threading.Event()

    def read(self, tenant: str | None = None) -> DefinedRoles:
        if tenant == "bo":
            self.released.wait(5)
        return super().read(tenant)


def test_a_slow_tenant_never_holds_back_another() -> None:
    definitions = Slow()
    roles = live(definitions)
    slow = threading.Thread(target=roles.current, args=("bo",))
    slow.start()
    try:
        answered = threading.Event()
        threading.Thread(target=lambda: (roles.current("pe"), answered.set())).start()
        assert answered.wait(1), "tenant pe waited for tenant bo's read"
    finally:
        definitions.released.set()
        slow.join()
