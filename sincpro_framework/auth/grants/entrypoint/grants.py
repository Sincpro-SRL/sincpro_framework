"""`Grants`: roles and groups in one object — what a provider resolves with, what an
administration screen calls, and the roles defined at runtime, every tenant's or one's own.

    grants = Grants(catalog, publisher=audit)                  # the context's store for everything
    JwtProvider(issuer, resolvers=[grants])                     # each identity, resolved per call

    grants.assign("user:ana", "billing.manager", tenant="bo", until=friday)
    grants.define("cash.desk", tenant="bo", implies={"billing.clerk"})     # bo's own, no deploy
    grants.assign("user:bea", "cash.desk", tenant="bo")
    grants.catalog("bo").describe()                                        # the admin screen

    @grants.allow                                       # a rule of the project's own
    def owners_give_admin(change: GrantChange) -> bool:
        return change.role != "billing.admin" or "billing.owner" in change.actor.roles

Context: every change is bounded by who acts (`administration`, `BoundedAdministration()` when
not given: the system and a superadmin may do anything; anyone else only in its own tenant, never
on itself, and only gives or takes away what it holds — `TrustedAdministration()` is Odoo's
rule), checked against the project's own rules (`allow`), checked under the tenant's lease
against what is stored now, written by compare-and-swap, and published. A tenant's own role is
named `<key>@<tenant>` in the catalog, so it never meets a role of the code or of every tenant;
here it is named by its key — a role named exactly is that one, else the tenant's own of that
name. One store holds everything —
assignments, roles defined, revocations, leases: the running context's (Redis), or the one given
(`DatabaseKeyValue` for the project's own database); `assignments=` and `revocations=` replace
one part. Permissions granted at runtime are those the code declares.
"""

from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from typing import Any

from sincpro_framework.auth.common.adapters.locks import AdministrationLocks
from sincpro_framework.auth.common.adapters.revocations import KeyValueRevocations
from sincpro_framework.auth.common.domain.administration import (
    AdministrationPolicy,
    BoundedAdministration,
)
from sincpro_framework.auth.common.domain.events import EventPublisher
from sincpro_framework.auth.common.domain.identity import Identity
from sincpro_framework.auth.common.domain.permissions import declared_permissions
from sincpro_framework.auth.common.domain.revocations import Revocations
from sincpro_framework.auth.grants.adapters.definitions import KeyValueRoleDefinitions
from sincpro_framework.auth.grants.adapters.stores import KeyValueGrants
from sincpro_framework.auth.grants.domain.assignments import Assignment, GrantStore
from sincpro_framework.auth.grants.domain.catalog import GrantCatalog
from sincpro_framework.auth.grants.domain.change_rules import (
    ChangeRule,
    ChangeRules,
    GrantChange,
)
from sincpro_framework.auth.grants.domain.definitions import RoleDefinition, RoleDefinitions
from sincpro_framework.auth.grants.services.administration import GrantAdministration
from sincpro_framework.auth.grants.services.grant_resolver import (
    GrantResolver,
    RoleMapping,
    ScopeCeiling,
)
from sincpro_framework.auth.grants.services.role_administration import RoleAdministration
from sincpro_framework.auth.grants.services.tenant_catalogs import TenantCatalogs
from sincpro_framework.data_layer.store import KeyValueStore


class Grants:
    """Roles held per tenant, resolved per call, administered at runtime."""

    def __init__(
        self,
        catalog: GrantCatalog,
        store: KeyValueStore | None = None,
        assignments: GrantStore | None = None,
        revocations: Revocations | None = None,
        definitions: RoleDefinitions | None = None,
        publisher: EventPublisher | None = None,
        administration: AdministrationPolicy | None = None,
        role_mapping: RoleMapping | None = None,
        scope_ceiling: ScopeCeiling | None = None,
        entitlements: bool = False,
        permissions: Iterable[str] | None = None,
        check_interval: timedelta = timedelta(seconds=1),
    ) -> None:
        """`catalog` — the roles the code declares. Everything else has a default:

        `store` holds assignments, roles defined, revocations and leases — the running
        context's when not given; `assignments` / `revocations` / `definitions` replace one
        part (`DatabaseGrants`, a shared `KeyValueRevocations`). `publisher` is told every change;
        `administration` bounds who may change what — `BoundedAdministration(superadmin=...)`
        names the permission of the platform's own administrators. `role_mapping`,
        `scope_ceiling`, `entitlements` shape what an identity resolves to. `permissions` —
        what may be granted at runtime — defaults to every `Permission` the code declares, read
        when a role is defined. `check_interval` — how often a replica reads the roles defined
        at runtime again.
        """
        self.assignments = assignments or KeyValueGrants(store)
        self.revocations = revocations or KeyValueRevocations(store)
        self.administration = administration or BoundedAdministration()
        self.rules = ChangeRules()
        """What `allow` registered — checked against every change."""
        locks = AdministrationLocks(store)
        self._catalogs = TenantCatalogs(
            catalog,
            definitions or KeyValueRoleDefinitions(store),
            permissions=(
                permissions
                if permissions is not None
                else lambda: declared_permissions().keys()
            ),
            check_interval=check_interval,
        )
        self._resolver = GrantResolver(
            self._catalogs,
            self.assignments,
            role_mapping=role_mapping,
            scope_ceiling=scope_ceiling,
            entitlements=entitlements,
            revocations=self.revocations,
        )
        self._grant_administration = GrantAdministration(
            self._catalogs,
            self.assignments,
            self.revocations,
            publisher,
            self.administration,
            locks,
            self.rules,
        )
        self._role_administration = RoleAdministration(
            self._catalogs,
            self.assignments,
            self.revocations,
            publisher,
            self.administration,
            locks,
            rules=self.rules,
        )

    def allow[F: Callable[[GrantChange], bool]](
        self, check: F | None = None, name: str | None = None
    ) -> Any:
        """Check `check` against every change an administrator makes — beside the bound, both
        must let it through; `False` refuses it (`PermissionDenied` naming the rule). The system
        passes. A decorator, bare or named:

            @grants.allow
            def owners_give_admin(change: GrantChange) -> bool: ...

            grants.allow(lambda change: change.action != GrantAction.REMOVE, name="nothing is removed")
        """

        def registered(given: F) -> F:
            self.rules.add(ChangeRule(name or getattr(given, "__name__", repr(given)), given))
            return given

        return registered if check is None else registered(check)

    def resolve(self, identity: Identity) -> Identity:
        """`identity` with the roles it holds in its tenant — their closure — and what they
        grant: what a provider asks (`resolvers=[grants]`)."""
        return self._resolver.resolve(identity)

    def assign(
        self,
        subject: str,
        role: str,
        tenant: str | None = None,
        until: datetime | None = None,
    ) -> None:
        """Assign `subject` the role `role` in `tenant` — until `until`, when temporary."""
        self._grant_administration.assign(subject, tenant, role, valid_until=until)

    def unassign(self, subject: str, role: str, tenant: str | None = None) -> None:
        """Take `role` back from `subject` in `tenant`."""
        self._grant_administration.unassign(subject, tenant, role)

    def set_level(
        self, subject: str, privilege: str, role: str | None, tenant: str | None = None
    ) -> None:
        """Hold `role` as the one level of `privilege` — `None` for no access."""
        self._grant_administration.set_level(subject, tenant, privilege, role)

    def entitle(self, tenant: str, entitlement: str) -> None:
        """The tenant has `entitlement` — the platform's to say."""
        self._grant_administration.entitle(tenant, entitlement)

    def withdraw(self, tenant: str, entitlement: str) -> None:
        """The tenant no longer has `entitlement` — the platform's to say."""
        self._grant_administration.withdraw(tenant, entitlement)

    def define(
        self,
        key: str,
        tenant: str | None = None,
        name: str = "",
        grants: Iterable[str] = (),
        implies: Iterable[str] = (),
        privilege: str | None = None,
        sequence: int = 0,
        disjoint: Iterable[str] = (),
        description: str = "",
    ) -> RoleDefinition:
        """Define the role `key` at runtime — every tenant's, or `tenant`'s own (held as
        `<key>@<tenant>`) — new or replacing the one of that key; the roles it implies or is
        disjoint from named as `assign` names them. Answers what was defined."""
        definition = RoleDefinition(
            key=key,
            tenant=tenant,
            name=name,
            grants=frozenset(str(one) for one in grants),
            implies=frozenset(implies),
            privilege=privilege,
            sequence=sequence,
            disjoint=frozenset(disjoint),
            description=description,
        )
        return self._role_administration.define(definition)

    def remove(self, key: str, tenant: str | None = None) -> None:
        """Remove the role `key` defined at runtime — every tenant's, or `tenant`'s own."""
        self._role_administration.remove(key, tenant)

    def catalog(self, tenant: str | None = None) -> GrantCatalog:
        """The catalog in effect for `tenant` — `describe()` is its administration screen."""
        return self._catalogs.catalog(tenant)

    def defined(self, tenant: str | None = None) -> tuple[RoleDefinition, ...]:
        """The roles defined at runtime for exactly this scope — what a screen may edit."""
        return self._catalogs.defined(tenant)

    def problems(self, tenant: str | None = None) -> dict[str, str]:
        """The roles defined for `tenant` that grant nothing, and why — to be fixed."""
        return self._catalogs.problems(tenant)

    def assignments_of(self, subject: str, tenant: str | None = None) -> list[Assignment]:
        """What `subject` was assigned in `tenant`, in force or not."""
        return self.assignments.assignments_of(subject, tenant)
