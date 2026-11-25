"""`GrantAdministration`: giving and taking roles, and a tenant's entitlements — checked against
the tenant's catalog and bounded by who acts. What `Grants` calls to assign; defining roles at
runtime is `RoleAdministration`'s.

    admin = GrantAdministration(catalog_or_tenant_catalogs, grants, revocations, publisher=audit)
    admin.set_level("user:ana", "bo", "invoicing", "billing.manager")   one level per privilege
    admin.assign("user:ana", "bo", "auditor", valid_until=friday)       temporary access
    admin.unassign("user:ana", "bo", "auditor")
    admin.entitle("bo", "e-invoicing")                                  the platform's alone

Context: a role is named as an administrator names it — exactly, else the tenant's own role of
that name (`EffectiveCatalog.resolve_key`) — and the project's own rules (`ChangeRules`) are
checked first. Who acts is the identity in play, and `policy` (`BoundedAdministration` by
default) bounds them: the system and a superadmin may do anything; anyone else only in their own
tenant, never on themselves, and only assign — or take away — what `AssignmentAuthority` allows.
Entitlements are what a tenant bought: the platform's to say. Every change is checked against the
tenant's catalog read now (`load`) while the tenant's administration lease is held
(`AdministrationLocks`) — so no
role is removed, nor changes shape, between the check and the write — bumps the permission
version it touches so the next call on any replica resolves anew, and is published when a
publisher was given. A role the catalog does not declare, two roles
never held together, two levels of one privilege: `InvalidGrant` (400), nothing written.
"""

from datetime import datetime

from sincpro_framework.auth.common.adapters.locks import AdministrationLocks
from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.domain.administration import (
    AdministrationPolicy,
    BoundedAdministration,
)
from sincpro_framework.auth.common.domain.events import SUBJECT, TENANT, EventPublisher
from sincpro_framework.auth.common.domain.identity import Identity
from sincpro_framework.auth.common.domain.revocations import Revocations, tenant_subject
from sincpro_framework.auth.common.infrastructure.event_publishing import publish_event
from sincpro_framework.auth.common.infrastructure.security_context import current_identity
from sincpro_framework.auth.grants.domain.assignment_authority import AssignmentAuthority
from sincpro_framework.auth.grants.domain.assignments import Assignment, GrantStore
from sincpro_framework.auth.grants.domain.catalog import CatalogSource, EffectiveCatalog
from sincpro_framework.auth.grants.domain.change_rules import (
    ChangeRules,
    GrantAction,
    GrantChange,
)
from sincpro_framework.auth.grants.domain.events import (
    EntitlementWithdrawn,
    LevelChanged,
    RoleAssigned,
    RoleUnassigned,
    TenantEntitled,
)


class GrantAdministration:
    """Assignments and entitlements, written once the tenant's catalog and `policy` allow them."""

    def __init__(
        self,
        catalog: CatalogSource,
        store: GrantStore,
        revocations: Revocations | None = None,
        publisher: EventPublisher | None = None,
        policy: AdministrationPolicy | None = None,
        locks: AdministrationLocks | None = None,
        rules: ChangeRules | None = None,
    ) -> None:
        """`locks` — the tenant leases every administration of auth shares; on the running
        context's store when not given. `rules` — the project's own, checked against every
        change."""
        self.catalog = catalog
        self.store = store
        self.revocations = revocations
        self.publisher = publisher
        self.policy = policy or BoundedAdministration()
        self.authority = AssignmentAuthority(self.policy)
        self.locks = locks or AdministrationLocks()
        self.rules = rules or ChangeRules()

    def resolve_key(self, role: str, tenant: str | None) -> str:
        """The catalog key `role` names in `tenant`, read now: itself when the catalog declares
        it, else the tenant's own role of that name."""
        return self.catalog.load(tenant).resolve_key(role, tenant)

    def _active_roles(self, subject: str, tenant: str | None) -> set[str]:
        """The roles assigned to `subject` in `tenant` and in force now."""
        now = self.store.now()
        return {
            one.role
            for one in self.store.assignments_of(subject, tenant)
            if one.is_active_at(now)
        }

    def _check_conflicts(self, effective: EffectiveCatalog, roles: set[str]) -> None:
        """`InvalidGrant` saying why `roles` cannot be held together."""
        conflicts = effective.catalog.conflicts(roles)
        if conflicts:
            raise exceptions.InvalidGrant("; ".join(conflicts))

    def _bump_permissions(self, subject: str) -> None:
        """Bump the subject's permission version."""
        if self.revocations is not None:
            self.revocations.bump_permissions(subject)

    def _check_scope(
        self, actor: Identity, subject: str, tenant: str | None, what: str
    ) -> None:
        """`actor` may administer `subject` in `tenant` — its scope, never itself."""
        self.policy.validate_tenant(actor, tenant, what)
        self.policy.validate_subject(actor, subject, what)

    def assign(
        self, subject: str, tenant: str | None, role: str, valid_until: datetime | None = None
    ) -> None:
        """Assign `role` — named as `resolve_key` reads it — until `valid_until`, when temporary.

        1. The project's rules allow it; who acts may administer `subject` in `tenant`, and may
           assign `role`.
        2. A role the tenant's catalog does not declare: `InvalidGrant`.
        3. Held with what the subject has now, it conflicts: `InvalidGrant`.
        Final: assigned, the subject's version bumped, published.
        """
        actor = current_identity()
        role = self.resolve_key(role, tenant)
        self.rules.check(
            GrantChange(actor, GrantAction.ASSIGN, tenant, subject=subject, role=role)
        )
        self._check_scope(actor, subject, tenant, "assign roles")
        with self.locks.acquire(tenant):
            effective = self.catalog.load(tenant)
            effective.check_assignable(role)
            self.authority.check_role(actor, effective, role, f"assigning {role}")
            self._check_conflicts(effective, self._active_roles(subject, tenant) | {role})
            self.store.assign(
                Assignment(subject=subject, tenant=tenant, role=role, valid_until=valid_until)
            )
            self._bump_permissions(subject)
        publish_event(
            self.publisher,
            RoleAssigned(
                entity_type=SUBJECT,
                entity_id=subject,
                tenant=tenant,
                role=role,
                valid_until=valid_until,
            ),
        )

    def unassign(self, subject: str, tenant: str | None, role: str) -> None:
        """Take `role` back — by whoever may administer `subject` in `tenant` and may assign it;
        a role no longer declared grants nothing and any of them may take it. The project's
        rules checked first; the subject's version bumped, published."""
        actor = current_identity()
        role = self.resolve_key(role, tenant)
        self.rules.check(
            GrantChange(actor, GrantAction.UNASSIGN, tenant, subject=subject, role=role)
        )
        self._check_scope(actor, subject, tenant, "take roles away")
        with self.locks.acquire(tenant):
            effective = self.catalog.load(tenant)
            if role in effective.catalog.roles:
                self.authority.check_role(actor, effective, role, f"taking {role} away")
            self.store.unassign(subject, tenant, role)
            self._bump_permissions(subject)
        publish_event(
            self.publisher,
            RoleUnassigned(entity_type=SUBJECT, entity_id=subject, tenant=tenant, role=role),
        )

    def set_level(
        self, subject: str, tenant: str | None, privilege: str, role: str | None
    ) -> None:
        """Hold `role` as the one level of `privilege` — `None` for no access.

        1. The project's rules allow it; who acts may administer `subject` in `tenant`.
        2. A role that is not a level of `privilege`: `InvalidGrant`.
        3. Who acts may assign `role` and every level taken away; `role` checked as `assign`
           is.
        Final: the other levels taken back, `role` assigned, the version bumped once, published.
        """
        actor = current_identity()
        role = None if role is None else self.resolve_key(role, tenant)
        self.rules.check(
            GrantChange(
                actor,
                GrantAction.SET_LEVEL,
                tenant,
                subject=subject,
                role=role,
                privilege=privilege,
            )
        )
        self._check_scope(actor, subject, tenant, "set levels")
        with self.locks.acquire(tenant):
            effective = self.catalog.load(tenant)
            levels = [one.key for one in effective.catalog.levels.get(privilege, [])]
            if role is not None and role not in levels:
                raise exceptions.InvalidGrant(f"{role} is not a level of {privilege}")
            active = self._active_roles(subject, tenant)
            for level in sorted((set(levels) & active) - {role}):
                self.authority.check_role(actor, effective, level, f"taking {level} away")
            if role is not None:
                effective.check_assignable(role)
                self.authority.check_role(actor, effective, role, f"setting {role}")
                self._check_conflicts(effective, (active - set(levels)) | {role})
            for level in levels:
                if level != role:
                    self.store.unassign(subject, tenant, level)
            if role is not None:
                self.store.assign(Assignment(subject=subject, tenant=tenant, role=role))
            self._bump_permissions(subject)
        publish_event(
            self.publisher,
            LevelChanged(
                entity_type=SUBJECT,
                entity_id=subject,
                tenant=tenant,
                privilege=privilege,
                level=role,
            ),
        )

    def entitle(self, tenant: str, entitlement: str) -> None:
        """The tenant has `entitlement` — the platform's to say, never a tenant's own
        administrator; the project's rules checked first; its version bumped, published."""
        actor = current_identity()
        self.rules.check(
            GrantChange(actor, GrantAction.ENTITLE, tenant, entitlement=entitlement)
        )
        self.policy.validate_tenant(actor, None, "entitle tenants")
        if entitlement not in self.catalog.cached(tenant).catalog.entitlements:
            raise exceptions.InvalidGrant(f"{entitlement} is not an entitlement")
        self.store.entitle(tenant, entitlement)
        if self.revocations is not None:
            self.revocations.bump_permissions(tenant_subject(tenant))
        publish_event(
            self.publisher,
            TenantEntitled(entity_type=TENANT, entity_id=tenant, entitlement=entitlement),
        )

    def withdraw(self, tenant: str, entitlement: str) -> None:
        """The tenant no longer has `entitlement` — the platform's to say; the project's rules
        checked first; its version bumped, published."""
        actor = current_identity()
        self.rules.check(
            GrantChange(actor, GrantAction.WITHDRAW, tenant, entitlement=entitlement)
        )
        self.policy.validate_tenant(actor, None, "withdraw entitlements")
        self.store.withdraw(tenant, entitlement)
        if self.revocations is not None:
            self.revocations.bump_permissions(tenant_subject(tenant))
        publish_event(
            self.publisher,
            EntitlementWithdrawn(
                entity_type=TENANT, entity_id=tenant, entitlement=entitlement
            ),
        )
