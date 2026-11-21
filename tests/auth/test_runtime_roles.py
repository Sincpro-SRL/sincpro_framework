"""Roles an administrator defines at runtime, beside the ones the code declares: granting only
what the code checks, never redefining a role of the code, checked by the catalog's own rules,
current on every replica within a second, published — and, when one no longer builds or the
store does not answer, never granting more than before."""

from datetime import timedelta
from typing import Any

import pytest

from sincpro_framework.auth import Identity, InvalidGrant, Permission
from sincpro_framework.auth.grants import (
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
from sincpro_framework.auth.grants.domain.definitions import DefinedRoles
from sincpro_framework.data_layer.store import InMemoryKeyValue, StoreUnavailable


class P(Permission):
    READ = "billing.invoice.read"
    ISSUE = "billing.invoice.issue"
    EXPORT = "billing.invoice.export"


invoicing = Privilege("invoicing", "Invoicing")
clerk = Role(
    "billing.clerk", "Clerk", grants=frozenset({P.READ}), privilege=invoicing, sequence=10
)
manager = Role(
    "billing.manager",
    "Manager",
    grants=frozenset({P.ISSUE}),
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


def test_a_role_defined_at_runtime_grants_what_it_says_and_what_it_implies() -> None:
    roles, grants, audit = live(), InMemoryGrants(), Recorder()
    admin = GrantAdministration(roles, grants, publisher=audit)
    admin.define_role(AUDITOR)
    admin.assign("user:ana", "bo", "billing.auditor")
    ana = GrantResolver(roles, grants).resolve(Identity.user("user:ana", tenant="bo"))
    assert ana.permissions == {P.EXPORT, P.READ}
    assert ana.roles == {"billing.auditor", "billing.clerk"}
    defined = audit.events[0]
    assert isinstance(defined, RoleDefined)
    assert (defined.entity_type, defined.entity_id, defined.grants, defined.implies) == (
        "auth.role",
        "billing.auditor",
        ["billing.invoice.export"],
        ["billing.clerk"],
    )


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


def test_a_role_another_one_implies_is_not_removed() -> None:
    admin = GrantAdministration(live(), InMemoryGrants())
    admin.define_role(AUDITOR)
    admin.define_role(
        RoleDefinition(key="billing.lead", implies=frozenset({"billing.auditor"}))
    )
    with pytest.raises(InvalidGrant, match="implied by billing.lead"):
        admin.remove_role("billing.auditor")


def test_another_replica_holds_it_once_it_checks_again() -> None:
    store, clock = InMemoryKeyValue(), Clock()
    here, there = live(KeyValueRoleDefinitions(store), clock), live(
        KeyValueRoleDefinitions(store), clock
    )
    assert "billing.auditor" not in there.current().roles
    GrantAdministration(here, InMemoryGrants()).define_role(AUDITOR)
    assert "billing.auditor" in here.current().roles  # the writer at once
    assert "billing.auditor" not in there.current().roles  # within check_every
    clock.now += 1.0
    assert "billing.auditor" in there.current().roles


def test_two_administrators_writing_one_version_never_lose_a_role() -> None:
    store = KeyValueRoleDefinitions(InMemoryKeyValue())
    assert store.write(0, [AUDITOR])
    assert not store.write(0, [AUDITOR])  # the second wrote over a version gone
    first = GrantAdministration(live(store), InMemoryGrants())
    second = GrantAdministration(live(store), InMemoryGrants())
    first.define_role(RoleDefinition(key="billing.lead", grants=frozenset({P.READ})))
    second.define_role(RoleDefinition(key="billing.viewer", grants=frozenset({P.READ})))
    assert {one.key for one in store.read().roles} == {
        "billing.auditor",
        "billing.lead",
        "billing.viewer",
    }


def test_a_stored_role_that_no_longer_builds_grants_nothing() -> None:
    definitions = InMemoryRoleDefinitions()
    definitions.write(
        0, [RoleDefinition(key="billing.x", implies=frozenset({"billing.gone"}))]
    )
    roles = live(definitions)
    assert roles.current() is catalog  # the code's catalog alone


class Unavailable(InMemoryRoleDefinitions):
    def __init__(self) -> None:
        super().__init__()
        self.down = False

    def read(self) -> DefinedRoles:
        if self.down:
            raise StoreUnavailable("redis is down")
        return super().read()


def test_a_store_that_does_not_answer_leaves_the_last_catalog_read() -> None:
    definitions, clock = Unavailable(), Clock()
    roles = live(definitions, clock)
    GrantAdministration(roles, InMemoryGrants()).define_role(AUDITOR)
    definitions.down = True
    clock.now += timedelta(minutes=5).total_seconds()
    assert "billing.auditor" in roles.current().roles


def test_a_catalog_of_code_alone_defines_nothing_at_runtime() -> None:
    with pytest.raises(Exception, match="on a LiveCatalog"):
        GrantAdministration(catalog, InMemoryGrants()).define_role(AUDITOR)
