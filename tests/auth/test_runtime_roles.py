"""Roles an administrator defines at runtime, beside the ones the code declares — for every
tenant (the platform's) or for one tenant (that tenant's, held as `<key>@<tenant>`) — and who may
give, define, change or take them away: granting only what the code checks, never meeting a role
of the code or of every tenant, never reaching another tenant, never coming back to life, never
handed by an administrator who does not hold them, never breaking another role, checked under the
tenant's lease against what is stored now, current on every replica within a second."""

import threading
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest

from sincpro_framework.auth import (
    BoundedAdministration,
    Identity,
    InMemoryRevocations,
    InvalidGrant,
    Permission,
    PermissionDenied,
    TrustedAdministration,
    Unauthenticated,
    security,
)
from sincpro_framework.auth.common.infrastructure.locks import AdministrationLocks
from sincpro_framework.auth.common.infrastructure.refreshed import Refreshed
from sincpro_framework.auth.grants import (
    GrantCatalog,
    GrantResolver,
    InMemoryGrants,
    InMemoryRoleDefinitions,
    KeyValueRoleDefinitions,
    Privilege,
    Role,
    RoleChanged,
    RoleDefined,
    RoleDefinition,
    RoleRemoved,
    RoleUnassigned,
)
from sincpro_framework.auth.grants.domain.definitions import DefinedRoles
from sincpro_framework.auth.grants.domain.giving import givable
from sincpro_framework.auth.grants.services.administration import GrantAdministration
from sincpro_framework.auth.grants.services.live_catalog import LiveCatalog
from sincpro_framework.auth.grants.services.role_administration import RoleAdministration
from sincpro_framework.data_layer.store import InMemoryKeyValue, LeaseTaken, StoreUnavailable


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
DESK = RoleDefinition(key="cash.desk", tenant="bo", grants=frozenset({P.READ}))
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
    def __init__(self, failing: bool = False) -> None:
        self.events: list[Any] = []
        self.failing = failing

    def publish(self, event: Any, /) -> None:
        self.events.append(event)
        if self.failing:
            raise RuntimeError("the audit's database is down")


def live(definitions: Any = None, clock: Clock | None = None) -> LiveCatalog:
    return LiveCatalog(
        catalog,
        definitions or InMemoryRoleDefinitions(),
        permissions=P,
        clock=clock or Clock(),
    )


def as_(identity: Identity) -> Any:
    return security.as_identity(identity)


def resolved(
    roles: LiveCatalog, grants: InMemoryGrants, subject: str, tenant: str
) -> Identity:
    return GrantResolver(roles, grants).resolve(Identity.user(subject, tenant=tenant))


# Defining


@pytest.mark.usefixtures("as_the_platform")
def test_a_role_defined_for_every_tenant_grants_what_it_says_and_what_it_implies() -> None:
    roles, grants, audit = live(), InMemoryGrants(), Recorder()
    RoleAdministration(roles, grants, publisher=audit).define(AUDITOR)
    GrantAdministration(roles, grants).assign("user:ana", "bo", "billing.auditor")
    ana = resolved(roles, grants, "user:ana", "bo")
    assert ana.permissions == {P.EXPORT, P.READ}
    assert ana.roles == {"billing.auditor", "billing.clerk"}
    (defined,) = audit.events
    assert isinstance(defined, RoleDefined) and defined.entity_id == "billing.auditor"


@pytest.mark.usefixtures("as_the_platform")
def test_a_tenants_role_is_held_by_its_tenants_name_for_that_tenant_alone() -> None:
    roles, grants = live(), InMemoryGrants()
    RoleAdministration(roles, grants).define(DESK)
    assert "cash.desk@bo" in roles.current("bo").roles
    assert "cash.desk@bo" not in roles.current("pe").roles
    assert "cash.desk@bo" not in roles.current().roles
    GrantAdministration(roles, grants).assign("user:ana", "bo", "cash.desk@bo")
    assert resolved(roles, grants, "user:ana", "bo").permissions == {P.READ}
    with pytest.raises(InvalidGrant, match="is not a role"):
        GrantAdministration(roles, grants).assign("user:bea", "pe", "cash.desk@bo")


@pytest.mark.usefixtures("as_the_platform")
def test_a_tenant_never_names_a_role_the_code_or_every_tenant_requires() -> None:
    roles, grants = live(), InMemoryGrants()
    RoleAdministration(roles, grants).define(
        RoleDefinition(key="billing.admin", tenant="bo")  # the code's name, in bo's own names
    )
    GrantAdministration(roles, grants).assign("user:friend", "bo", "billing.admin@bo")
    friend = resolved(roles, grants, "user:friend", "bo")
    assert "billing.admin" not in friend.roles  # `auth.role("billing.admin")` refuses
    assert friend.roles == {"billing.admin@bo"}
    with pytest.raises(InvalidGrant, match="not part of a key"):
        RoleAdministration(roles, grants).define(RoleDefinition(key="x@pe", tenant="bo"))


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


@pytest.mark.usefixtures("as_the_platform")
def test_a_scope_holds_so_many_roles_and_a_role_implies_so_many() -> None:
    roles = RoleAdministration(live(), InMemoryGrants(), max_roles=2, max_implies=1)
    roles.define(RoleDefinition(key="a.one", tenant="bo"))
    roles.define(RoleDefinition(key="a.two", tenant="bo"))
    with pytest.raises(InvalidGrant, match="no more than 2 roles"):
        roles.define(RoleDefinition(key="a.three", tenant="bo"))
    with pytest.raises(InvalidGrant, match="implies more than 1"):
        roles.define(
            RoleDefinition(
                key="a.one", tenant="bo", implies=frozenset({"billing.clerk", "portal"})
            )
        )


# Who may give, define, change and take away


def test_a_tenants_administrator_defines_and_gives_only_what_it_holds() -> None:
    roles, grants = live(), InMemoryGrants()
    defining, assigning = RoleAdministration(roles, grants), GrantAdministration(
        roles, grants
    )
    with as_(BO_ADMIN):
        defining.define(
            RoleDefinition(key="cash.till", tenant="bo", implies=frozenset({"billing.clerk"}))
        )
        assigning.assign(
            "user:ana", "bo", "cash.till@bo"
        )  # its tenant's own, of what it holds
        with pytest.raises(PermissionDenied, match="needs billing.invoice.export"):
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
    owner = RoleDefinition(key="cash.owner", tenant="bo", grants=frozenset({P.EXPORT}))
    with as_(PLATFORM):
        RoleAdministration(roles, grants).define(owner)
        GrantAdministration(roles, grants).assign("user:owner", "bo", "cash.owner@bo")
        GrantAdministration(roles, grants).assign("user:owner", "bo", "billing.manager")
    clerk_admin = Identity.user(
        "user:clerk-admin", tenant="bo", roles={"billing.clerk"}, permissions={P.READ}
    )
    with as_(clerk_admin):
        with pytest.raises(PermissionDenied, match="cash.owner@bo"):
            GrantAdministration(roles, grants).unassign("user:owner", "bo", "cash.owner@bo")
        with pytest.raises(PermissionDenied, match="billing.manager"):
            GrantAdministration(roles, grants).set_level(
                "user:owner", "bo", "invoicing", None
            )
        with pytest.raises(PermissionDenied, match="billing.invoice.export"):
            RoleAdministration(roles, grants).define(
                owner.model_copy(update={"grants": frozenset({P.READ})})
            )
        with pytest.raises(PermissionDenied, match="billing.invoice.export"):
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
    defining.define(DESK)
    defining.define(DESK.model_copy(update={"name": "Desk"}))  # unheld: anything may change
    GrantAdministration(roles, grants).assign("user:ana", "bo", "portal")
    GrantAdministration(roles, grants).assign("user:ana", "bo", "cash.desk@bo")
    with pytest.raises(InvalidGrant, match="is held"):  # ana: portal and clerk together
        defining.define(DESK.model_copy(update={"implies": frozenset({"billing.clerk"})}))
    defining.define(DESK.model_copy(update={"name": "Front desk"}))  # its shape kept
    defining.define(AUDITOR)
    with pytest.raises(InvalidGrant, match="is held"):
        defining.define(AUDITOR.model_copy(update={"implies": frozenset()}))


@pytest.mark.usefixtures("as_the_platform")
def test_a_role_that_may_take_something_away_refuses_the_tokens_of_its_scope() -> None:
    roles, grants, revocations, audit = (
        live(),
        InMemoryGrants(),
        InMemoryRevocations(),
        Recorder(),
    )
    defining = RoleAdministration(roles, grants, revocations, publisher=audit)
    defining.define(DESK.model_copy(update={"grants": frozenset({P.READ, P.EXPORT})}))
    defining.define(
        RoleDefinition(key="cash.lead", tenant="bo", implies=frozenset({"cash.desk@bo"}))
    )
    GrantAdministration(roles, grants).assign("user:cy", "bo", "cash.lead@bo")  # through it
    cy, bea = Identity.user("user:cy", tenant="bo"), Identity.user("user:bea", tenant="pe")
    before, elsewhere = revocations.permission_epoch(cy), revocations.permission_epoch(bea)
    defining.define(
        DESK.model_copy(update={"grants": frozenset({P.READ, P.EXPORT}), "name": "Desk"})
    )
    assert revocations.permission_epoch(cy) == before  # only renamed: nothing taken
    defining.define(DESK)  # grants shrunk
    assert revocations.permission_epoch(cy) == before + 1
    assert revocations.permission_epoch(bea) == elsewhere  # another tenant's untouched
    events = len(audit.events)
    defining.define(DESK)  # saved again as it is — what a retry does
    assert revocations.permission_epoch(cy) == before + 2
    assert len(audit.events) == events  # nothing changed: nothing published
    changed = audit.events[-1]
    assert isinstance(changed, RoleChanged)
    assert changed.previous["grants"] == sorted({P.READ, P.EXPORT}) and changed.grants == [
        P.READ
    ]
    defining.define(AUDITOR)
    defining.define(AUDITOR.model_copy(update={"grants": frozenset()}))  # every tenant's
    assert revocations.permission_epoch(bea) == elsewhere + 1


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
    defining.define(DESK)
    GrantAdministration(roles, grants).assign("user:ana", "bo", "cash.desk@bo")
    defining.remove("cash.desk", "bo")
    assert grants.held("user:ana", "bo").roles == frozenset()
    assert [type(one) for one in audit.events] == [
        RoleDefined,
        RoleRemoved,
        RoleDefined,
        RoleRemoved,
        RoleUnassigned,
    ]


@pytest.mark.usefixtures("as_the_platform")
def test_an_assignment_left_of_a_removed_role_passes_no_role_check() -> None:
    roles, grants = live(), InMemoryGrants()
    RoleAdministration(roles, grants).define(AUDITOR)
    GrantAdministration(roles, grants).assign("user:sup", "pe", "billing.auditor")
    RoleAdministration(roles, grants).remove("billing.auditor")
    assert "billing.auditor" in grants.held("user:sup", "pe").roles  # left, in every tenant
    sup = resolved(roles, grants, "user:sup", "pe")
    assert sup.roles == frozenset() and sup.permissions == frozenset()


@pytest.mark.usefixtures("as_the_platform")
def test_a_role_another_one_implies_is_not_removed() -> None:
    defining = RoleAdministration(live(), InMemoryGrants())
    defining.define(AUDITOR)
    defining.define(
        RoleDefinition(key="billing.lead", implies=frozenset({"billing.auditor"}))
    )
    with pytest.raises(InvalidGrant, match="implied by billing.lead"):
        defining.remove("billing.auditor")


def test_a_change_never_leaves_another_role_broken() -> None:
    roles, grants = live(), InMemoryGrants()
    defining = RoleAdministration(roles, grants)
    with as_(PLATFORM):
        defining.define(RoleDefinition(key="x", tenant="bo"))
        defining.define(
            RoleDefinition(key="y", tenant="bo", implies=frozenset({"x@bo", "portal"}))
        )
        with pytest.raises(InvalidGrant, match="y@bo"):  # y would hold portal and clerk
            defining.define(
                RoleDefinition(key="x", tenant="bo", implies=frozenset({"billing.clerk"}))
            )
        with pytest.raises(InvalidGrant, match="a level is every tenant's"):
            defining.define(
                RoleDefinition(
                    key="top",
                    tenant="bo",
                    privilege="invoicing",
                    sequence=30,
                    implies=frozenset({"billing.manager"}),
                )
            )
    assert "y@bo" in roles.latest("bo").catalog.roles


@pytest.mark.usefixtures("as_the_platform")
def test_a_role_held_through_another_keeps_its_shape() -> None:
    roles, grants = live(), InMemoryGrants()
    defining = RoleAdministration(roles, grants)
    defining.define(RoleDefinition(key="r1", tenant="bo"))
    defining.define(RoleDefinition(key="r2", tenant="bo", implies=frozenset({"r1@bo"})))
    GrantAdministration(roles, grants).assign("user:bob", "bo", "portal")
    GrantAdministration(roles, grants).assign("user:bob", "bo", "r2@bo")
    with pytest.raises(InvalidGrant, match="r1@bo is held"):  # bob: portal and clerk
        defining.define(
            RoleDefinition(key="r1", tenant="bo", implies=frozenset({"billing.clerk"}))
        )


class BlinkingGrants(InMemoryGrants):
    """Its first `unassign` fails — the store blinked."""

    def __init__(self) -> None:
        super().__init__()
        self.blinks = 1

    def unassign(self, subject: str, tenant: str | None, role: str) -> None:
        if self.blinks:
            self.blinks -= 1
            raise StoreUnavailable("redis blinked")
        super().unassign(subject, tenant, role)


@pytest.mark.usefixtures("as_the_platform")
def test_a_removal_asked_again_finishes_what_a_failure_left() -> None:
    roles, grants, revocations, audit = (
        live(),
        BlinkingGrants(),
        InMemoryRevocations(),
        Recorder(),
    )
    defining = RoleAdministration(roles, grants, revocations, publisher=audit)
    defining.define(DESK)
    GrantAdministration(roles, grants).assign("user:ana", "bo", "cash.desk@bo")
    with pytest.raises(StoreUnavailable):
        defining.remove("cash.desk", "bo")
    defining.remove("cash.desk", "bo")  # retired already: what was left is finished
    assert grants.held("user:ana", "bo").roles == frozenset()
    assert [type(one) for one in audit.events] == [RoleDefined, RoleRemoved, RoleUnassigned]
    assert revocations.permission_version("tenant:bo") == 1


@pytest.mark.usefixtures("as_the_platform")
def test_a_key_used_at_runtime_is_never_granted_by_an_old_assignment() -> None:
    definitions, grants = InMemoryRoleDefinitions(), InMemoryGrants()
    roles = live(definitions)
    RoleAdministration(roles, grants).define(AUDITOR)
    GrantAdministration(roles, grants).assign("user:sup", "pe", "billing.auditor")
    RoleAdministration(roles, grants).remove("billing.auditor")
    auditor_in_code = Role("billing.auditor", "Auditor", grants={P.EXPORT})
    later = LiveCatalog(
        GrantCatalog([manager, admin_role, portal, auditor_in_code]),
        definitions,
        permissions=P,
        clock=Clock(),
    )  # a deploy months later declares the key
    sup = GrantResolver(later, grants).resolve(Identity.user("user:sup", tenant="pe"))
    assert P.EXPORT not in sup.permissions and "billing.auditor" not in sup.roles
    assert "rename it in code" in later.problems("pe")["billing.auditor"]
    with pytest.raises(InvalidGrant, match="never given again"):
        GrantAdministration(later, grants).assign("user:new", "pe", "billing.auditor")


def test_giving_a_lattice_of_a_tenants_roles_costs_what_its_roles_count() -> None:
    class Counting(BoundedAdministration):
        calls = 0

        def holding(self, *args: Any, **kwargs: Any) -> None:
            Counting.calls += 1
            super().holding(*args, **kwargs)

    roles, layer = live(), ["billing.clerk"]
    with as_(PLATFORM):
        for depth in range(14):
            names = [f"l{depth}.{side}" for side in "ab"]
            for name in names:
                RoleAdministration(roles, InMemoryGrants()).define(
                    RoleDefinition(key=name, tenant="bo", implies=frozenset(layer))
                )
            layer = [f"{name}@bo" for name in names]
    givable(Counting(), BO_ADMIN, roles.latest("bo"), layer[0], "assigning")
    assert Counting.calls <= 2 * 14 + 1  # each role once — never each path (2^14)


def test_a_scope_never_built_is_not_asked_again_while_the_store_is_down() -> None:
    reads: list[Any] = []

    def down(scope: Any) -> Any:
        reads.append(scope)
        raise StoreUnavailable("redis is down")

    clock = Clock()
    held: Refreshed[str] = Refreshed(
        read=down, build=str, what="roles", unread=lambda: "the code's", clock=clock
    )
    assert {held.get("bo") for _ in range(10)} == {"the code's"}
    assert len(reads) == 1
    clock.now += 1.0
    held.get("bo")
    assert len(reads) == 2


def test_a_store_that_lost_a_document_never_answers_with_what_was_built_of_it() -> None:
    store, clock = InMemoryKeyValue(), Clock()
    roles = live(KeyValueRoleDefinitions(store), clock)
    with as_(PLATFORM):
        RoleAdministration(roles, InMemoryGrants()).define(DESK)
        assert "cash.desk@bo" in roles.current("bo").roles
        store.delete("sincpro:auth:roles:bo")  # evicted: its versions start again from 1
        RoleAdministration(roles, InMemoryGrants()).define(
            RoleDefinition(key="cash.other", tenant="bo", grants=frozenset({P.READ}))
        )
    clock.now += 1.0
    in_effect = roles.current("bo").roles
    assert "cash.other@bo" in in_effect and "cash.desk@bo" not in in_effect


def test_a_role_that_no_longer_builds_can_be_fixed_and_removed() -> None:
    roles, grants = live(), InMemoryGrants()
    lead = RoleDefinition(
        key="cash.lead", tenant="bo", implies=frozenset({"billing.auditor"})
    )
    with as_(PLATFORM):
        RoleAdministration(roles, grants).define(AUDITOR)
        RoleAdministration(roles, grants).define(lead)
        RoleAdministration(roles, grants).remove("billing.auditor")  # every tenant's: retired
    assert "cash.lead@bo" in roles.problems("bo")
    with as_(BO_ADMIN):
        RoleAdministration(roles, grants).define(
            lead.model_copy(update={"implies": frozenset({"billing.clerk"})})
        )
    assert "cash.lead@bo" in roles.current("bo").roles and not roles.problems("bo")
    with as_(BO_ADMIN):
        RoleAdministration(roles, grants).remove("cash.lead", "bo")


# Stored, read, raced


def test_a_change_is_checked_against_what_is_stored_never_a_stale_catalog() -> None:
    store, clock = InMemoryKeyValue(), Clock()
    here = live(KeyValueRoleDefinitions(store), clock)
    there = live(KeyValueRoleDefinitions(store), clock)
    with as_(PLATFORM):
        RoleAdministration(here, InMemoryGrants()).define(DESK)
    assert "cash.desk@bo" in there.current("bo").roles  # there read it, and holds it a second
    with as_(PLATFORM):
        RoleAdministration(here, InMemoryGrants()).define(
            RoleDefinition(key="cash.export", tenant="bo", grants=frozenset({P.EXPORT}))
        )
    with as_(BO_ADMIN), pytest.raises(PermissionDenied, match="billing.invoice.export"):
        RoleAdministration(there, InMemoryGrants()).define(
            DESK.model_copy(update={"implies": frozenset({"cash.export@bo"})})
        )


def test_administering_a_tenant_is_one_at_a_time() -> None:
    store = InMemoryKeyValue()
    roles, grants = live(), InMemoryGrants()
    busy = AdministrationLocks(store, wait=timedelta(0))
    with as_(PLATFORM), busy.held("bo"):
        with pytest.raises(LeaseTaken):
            GrantAdministration(roles, grants, locks=busy).assign("user:ana", "bo", "portal")
        GrantAdministration(roles, grants, locks=busy).assign("user:ana", "pe", "portal")


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


@pytest.mark.usefixtures("as_the_platform")
def test_a_publisher_that_fails_never_undoes_a_change() -> None:
    roles, grants = live(), InMemoryGrants()
    defining = RoleAdministration(roles, grants, publisher=Recorder(failing=True))
    defining.define(DESK)
    for subject in ("user:a", "user:b", "user:c"):
        GrantAdministration(roles, grants).assign(subject, "bo", "cash.desk@bo")
    defining.remove("cash.desk", "bo")
    assert all(not grants.held(one, "bo").roles for one in ("user:a", "user:b", "user:c"))


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
        self.reads = 0

    def read(self, scope: str | None = None) -> DefinedRoles:
        self.reads += 1
        if self.down:
            raise StoreUnavailable("redis is down")
        return super().read(scope)


@pytest.mark.usefixtures("as_the_platform")
def test_a_store_that_does_not_answer_leaves_the_last_catalog_and_is_not_hammered() -> None:
    definitions, clock = Unavailable(), Clock()
    roles = live(definitions, clock)
    defining = RoleAdministration(roles, InMemoryGrants())
    defining.define(DESK)
    assert "cash.desk@bo" in roles.current("bo").roles
    defining.define(RoleDefinition(key="cash.lead", tenant="bo", grants=frozenset({P.READ})))
    definitions.down = True  # right after the write, before any read
    clock.now += timedelta(minutes=5).total_seconds()
    assert "cash.desk@bo" in roles.current("bo").roles
    reads = definitions.reads
    for _ in range(50):
        roles.current("bo")
    assert definitions.reads == reads  # asked again only once check_every passed


class Slow(InMemoryRoleDefinitions):
    """A store whose reads for tenant bo hang until released."""

    def __init__(self) -> None:
        super().__init__()
        self.released = threading.Event()

    def read(self, scope: str | None = None) -> DefinedRoles:
        if scope == "bo":
            self.released.wait(5)
        return super().read(scope)


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


def test_stale_marked_during_a_read_is_never_lost() -> None:
    reading, release = threading.Event(), threading.Event()
    versions = iter([(1,), (2,), (2,)])

    def read(scope: Any) -> tuple[tuple[int, ...], Any]:
        reading.set()
        release.wait(5)
        found = next(versions)
        return found, found

    held = Refreshed(read=read, build=lambda content: content, what="a test", clock=Clock())
    first = threading.Thread(target=held.get, args=("bo",))
    first.start()
    assert reading.wait(5)
    marking = threading.Thread(target=held.stale, args=("bo",))  # the writer's, mid-read
    marking.start()
    release.set()
    first.join(5)
    marking.join(5)
    assert held.get("bo") == (2,)  # read again, not kept for check_every
