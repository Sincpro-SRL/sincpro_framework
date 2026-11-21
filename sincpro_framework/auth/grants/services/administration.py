"""`GrantAdministration`: giving and taking roles, and a tenant's entitlements — checked against
the tenant's catalog and bounded by who acts. What `Grants` calls to assign; defining roles at
runtime is `RoleAdministration`'s.

    admin = GrantAdministration(catalog_or_live, grants, revocations, publisher=audit)
    admin.set_level("user:ana", "bo", "invoicing", "billing.manager")   one level per privilege
    admin.assign("user:ana", "bo", "auditor", valid_until=friday)       temporary access
    admin.unassign("user:ana", "bo", "auditor")
    admin.entitle("bo", "e-invoicing")                                  the platform's alone

Context: who acts is the identity in play, and `policy` (`BoundedAdministration` by default)
holds them: the system and a superadmin may do anything; anyone else only in their own tenant,
never on themselves, and only give — or take away — what `givable` allows. Entitlements are what
a tenant bought: the platform's to say. Every change is checked against the tenant's catalog read
now (`latest`) while the tenant's administration lease is held (`AdministrationLocks`) — so no
role is removed, nor changes shape, between the check and the write — bumps the permission
version it touches so the next call on any replica resolves anew, and is published when a
publisher was given. A role the catalog does not declare, two roles
never held together, two levels of one privilege: `InvalidGrant` (400), nothing written.
"""

from datetime import datetime

from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.domain.administration import (
    AdministrationPolicy,
    BoundedAdministration,
)
from sincpro_framework.auth.domain.events import SUBJECT, TENANT, EventPublisher
from sincpro_framework.auth.domain.identity import Identity
from sincpro_framework.auth.domain.revocations import Revocations, tenant_subject
from sincpro_framework.auth.grants.domain.assignments import Assignment, GrantStore
from sincpro_framework.auth.grants.domain.catalog import CatalogSource, Composition
from sincpro_framework.auth.grants.domain.events import (
    EntitlementWithdrawn,
    LevelChanged,
    RoleAssigned,
    RoleUnassigned,
    TenantEntitled,
)
from sincpro_framework.auth.grants.domain.giving import givable
from sincpro_framework.auth.infrastructure.announcing import announce
from sincpro_framework.auth.infrastructure.locks import AdministrationLocks
from sincpro_framework.auth.infrastructure.security_context import current_identity


def _givable_key(composition: Composition, role: str) -> None:
    """`InvalidGrant` for a role the tenant's catalog does not declare, or a key retired — one
    removed at runtime is never given again."""
    composition.catalog.role(role)
    if role in composition.retired:
        raise exceptions.InvalidGrant(
            f"{role} was removed at runtime: it is never given again"
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
    ) -> None:
        """`locks` — the tenant leases every administration of auth shares; on the running
        context's store when not given."""
        self.catalog = catalog
        self.store = store
        self.revocations = revocations
        self.publisher = publisher
        self.policy = policy or BoundedAdministration()
        self.locks = locks or AdministrationLocks()

    def _in_force(self, subject: str, tenant: str | None) -> set[str]:
        """The roles assigned to `subject` in `tenant` and in force now."""
        now = self.store.now()
        return {
            one.role
            for one in self.store.assignments_of(subject, tenant)
            if one.holds_at(now)
        }

    def _allowed(self, composition: Composition, roles: set[str]) -> None:
        """`InvalidGrant` saying why `roles` cannot be held together."""
        conflicts = composition.catalog.conflicts(roles)
        if conflicts:
            raise exceptions.InvalidGrant("; ".join(conflicts))

    def _changed(self, subject: str) -> None:
        """Bump the subject's permission version."""
        if self.revocations is not None:
            self.revocations.bump_permissions(subject)

    def _administering(
        self, actor: Identity, subject: str, tenant: str | None, what: str
    ) -> None:
        """`actor` may administer `subject` in `tenant` — its scope, never itself."""
        self.policy.in_scope(actor, tenant, what)
        self.policy.on_subject(actor, subject, what)

    def assign(
        self, subject: str, tenant: str | None, role: str, valid_until: datetime | None = None
    ) -> None:
        """Give `role` — until `valid_until`, when temporary.

        1. Who acts may administer `subject` in `tenant`, and may give `role`.
        2. A role the tenant's catalog does not declare: `InvalidGrant`.
        3. Held with what the subject has now, it conflicts: `InvalidGrant`.
        Final: assigned, the subject's version bumped, published.
        """
        actor = current_identity()
        self._administering(actor, subject, tenant, "assign roles")
        with self.locks.held(tenant):
            composition = self.catalog.latest(tenant)
            _givable_key(composition, role)
            givable(self.policy, actor, composition, role, f"assigning {role}")
            self._allowed(composition, self._in_force(subject, tenant) | {role})
            self.store.assign(
                Assignment(subject=subject, tenant=tenant, role=role, valid_until=valid_until)
            )
            self._changed(subject)
        announce(
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
        """Take `role` back — by whoever may administer `subject` in `tenant` and may give it; a
        role no longer declared grants nothing and any of them may take it. The subject's version
        bumped, published."""
        actor = current_identity()
        self._administering(actor, subject, tenant, "take roles away")
        with self.locks.held(tenant):
            composition = self.catalog.latest(tenant)
            if role in composition.catalog.roles:
                givable(self.policy, actor, composition, role, f"taking {role} away")
            self.store.unassign(subject, tenant, role)
            self._changed(subject)
        announce(
            self.publisher,
            RoleUnassigned(entity_type=SUBJECT, entity_id=subject, tenant=tenant, role=role),
        )

    def set_level(
        self, subject: str, tenant: str | None, privilege: str, role: str | None
    ) -> None:
        """Hold `role` as the one level of `privilege` — `None` for no access.

        1. Who acts may administer `subject` in `tenant`.
        2. A role that is not a level of `privilege`: `InvalidGrant`.
        3. Who acts may give `role` and every level taken away; `role` checked as `assign` is.
        Final: the other levels taken back, `role` given, the version bumped once, published.
        """
        actor = current_identity()
        self._administering(actor, subject, tenant, "set levels")
        with self.locks.held(tenant):
            composition = self.catalog.latest(tenant)
            levels = [one.key for one in composition.catalog.levels.get(privilege, [])]
            if role is not None and role not in levels:
                raise exceptions.InvalidGrant(f"{role} is not a level of {privilege}")
            held = self._in_force(subject, tenant)
            for level in sorted((set(levels) & held) - {role}):
                givable(self.policy, actor, composition, level, f"taking {level} away")
            if role is not None:
                _givable_key(composition, role)
                givable(self.policy, actor, composition, role, f"setting {role}")
                self._allowed(composition, (held - set(levels)) | {role})
            for level in levels:
                if level != role:
                    self.store.unassign(subject, tenant, level)
            if role is not None:
                self.store.assign(Assignment(subject=subject, tenant=tenant, role=role))
            self._changed(subject)
        announce(
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
        administrator; its version bumped, published."""
        self.policy.in_scope(current_identity(), None, "entitle tenants")
        if entitlement not in self.catalog.in_effect(tenant).catalog.entitlements:
            raise exceptions.InvalidGrant(f"{entitlement} is not an entitlement")
        self.store.entitle(tenant, entitlement)
        if self.revocations is not None:
            self.revocations.bump_permissions(tenant_subject(tenant))
        announce(
            self.publisher,
            TenantEntitled(entity_type=TENANT, entity_id=tenant, entitlement=entitlement),
        )

    def withdraw(self, tenant: str, entitlement: str) -> None:
        """The tenant no longer has `entitlement` — the platform's to say; its version bumped,
        published."""
        self.policy.in_scope(current_identity(), None, "withdraw entitlements")
        self.store.withdraw(tenant, entitlement)
        if self.revocations is not None:
            self.revocations.bump_permissions(tenant_subject(tenant))
        announce(
            self.publisher,
            EntitlementWithdrawn(
                entity_type=TENANT, entity_id=tenant, entitlement=entitlement
            ),
        )
