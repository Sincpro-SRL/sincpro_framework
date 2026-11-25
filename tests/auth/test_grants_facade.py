"""`Grants`, the one object a project holds for roles: what a provider resolves with, what an
administration screen calls — assigning, defining a tenant's own roles by their key — bounded by
who acts, asked of the project's own rules, on one store, published."""

from typing import Any

import pytest

from sincpro_framework.auth import (
    Identity,
    InvalidGrant,
    Permission,
    PermissionDenied,
    TrustedAdministration,
    security,
)
from sincpro_framework.auth.grants import (
    GrantAction,
    GrantCatalog,
    GrantChange,
    Grants,
    Privilege,
    Role,
    RoleAssigned,
    RoleDefined,
)
from sincpro_framework.data_layer.store import InMemoryKeyValue


class P(Permission):
    READ = "facade.invoice.read"
    ISSUE = "facade.invoice.issue"
    EXPORT = "facade.invoice.export"


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
owner = Role("billing.owner", "Owner", implies=(manager,))
CATALOG = GrantCatalog([owner])
PLATFORM = Identity.system("the platform")
BO_OWNER = Identity.user(
    "user:root-bo",
    tenant="bo",
    roles={"billing.owner", "billing.manager", "billing.clerk"},
    permissions={P.READ, P.ISSUE},
)


class Recorder:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def publish(self, event: Any, /) -> None:
        self.events.append(event)


def grants(**options: Any) -> Grants:
    return Grants(CATALOG, store=InMemoryKeyValue(), **options)


def resolved(held: Grants, subject: str, tenant: str) -> Identity:
    return held.resolve(Identity.user(subject, tenant=tenant))


def test_what_is_assigned_is_what_a_call_resolves() -> None:
    audit = Recorder()
    held = grants(publisher=audit)
    with security.as_identity(PLATFORM):
        held.assign("user:ana", "billing.manager", tenant="bo")
    ana = resolved(held, "user:ana", "bo")
    assert {"billing.manager", "billing.clerk"} <= set(ana.roles)
    assert {P.READ, P.ISSUE} <= set(ana.permissions)
    assert resolved(held, "user:ana", "pe").permissions == frozenset()  # another tenant
    assert [type(one) for one in audit.events] == [RoleAssigned]
    assert [one.role for one in held.assignments_of("user:ana", "bo")] == ["billing.manager"]


def test_a_tenants_own_role_is_named_by_its_key() -> None:
    audit = Recorder()
    held = grants(publisher=audit)
    with security.as_identity(BO_OWNER):
        defined = held.define(
            "cash.desk", tenant="bo", name="Desk", implies={"billing.clerk"}
        )
        held.assign("user:bea", "cash.desk", tenant="bo")
    assert defined.catalog_key == "cash.desk@bo"
    bea = resolved(held, "user:bea", "bo")
    assert "cash.desk@bo" in bea.roles and P.READ in bea.permissions
    assert "cash.desk@bo" in held.catalog("bo").roles
    assert "cash.desk@bo" not in held.catalog("pe").roles
    assert [one.key for one in held.defined("bo")] == ["cash.desk"]
    assert held.problems("bo") == {}
    assert RoleDefined in [type(one) for one in audit.events]


def test_a_role_of_the_code_is_never_shadowed_by_a_tenants() -> None:
    held = grants()
    with security.as_identity(BO_OWNER):
        held.define("billing.clerk", tenant="bo", grants={P.READ})  # bo's, beside the code's
        held.assign("user:ana", "billing.clerk", tenant="bo")
    ana = resolved(held, "user:ana", "bo")
    assert "billing.clerk" in ana.roles and "billing.clerk@bo" not in ana.roles


def test_what_may_be_granted_is_what_the_code_declares() -> None:
    held = grants()
    with security.as_identity(PLATFORM):
        held.define("billing.auditor", grants={P.EXPORT})
        with pytest.raises(InvalidGrant):
            held.define("billing.everything", grants={"facade.anything.at.all"})


def test_an_administrator_gives_only_what_it_holds_unless_trusted() -> None:
    bo_clerk_admin = Identity.user(
        "user:clerk", tenant="bo", roles={"billing.clerk"}, permissions={P.READ}
    )
    bounded, trusted = grants(), grants(administration=TrustedAdministration())
    with security.as_identity(bo_clerk_admin):
        with pytest.raises(PermissionDenied):
            bounded.assign("user:ana", "billing.manager", tenant="bo")
        bounded.assign("user:ana", "billing.clerk", tenant="bo")
        trusted.assign("user:ana", "billing.manager", tenant="bo")
        with pytest.raises(PermissionDenied):
            bounded.assign("user:ana", "billing.clerk", tenant="pe")  # another tenant


def test_the_projects_own_rules_are_asked_of_every_change() -> None:
    held = grants()
    seen: list[GrantChange] = []

    @held.allow
    def only_owners_give_managers(change: GrantChange) -> bool:
        seen.append(change)
        return change.role != "billing.manager" or "billing.owner" in change.actor.roles

    held.allow(lambda change: change.action != GrantAction.REMOVE, name="nothing is removed")
    bo_manager = Identity.user(
        "user:manager",
        tenant="bo",
        roles={"billing.manager", "billing.clerk"},
        permissions={P.READ, P.ISSUE},
    )
    with security.as_identity(bo_manager):
        with pytest.raises(PermissionDenied, match="only_owners_give_managers"):
            held.assign("user:ana", "billing.manager", tenant="bo")
    with security.as_identity(BO_OWNER):
        held.assign("user:ana", "billing.manager", tenant="bo")
        held.define("cash.desk", tenant="bo", grants={P.READ})
        with pytest.raises(PermissionDenied, match="nothing is removed"):
            held.remove("cash.desk", tenant="bo")
    with security.as_identity(PLATFORM):
        held.remove("cash.desk", tenant="bo")  # the system passes
    first = seen[0]
    assert (first.action, first.subject, first.role, first.tenant) == (
        "assign",
        "user:ana",
        "billing.manager",
        "bo",
    )
    assert first.actor.subject == "user:manager"
