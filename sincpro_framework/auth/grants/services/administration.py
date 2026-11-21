"""`GrantAdministration`: giving and taking roles and entitlements, and defining roles at runtime,
checked against the tenant's catalog and bounded by who acts — what an administration screen or a
use case of the project calls.

    admin = GrantAdministration(live, grants, revocations, publisher=audit)
    admin.set_level("user:ana", "bo", "invoicing", "billing.manager")   one level per privilege
    admin.assign("user:ana", "bo", "auditor", valid_until=friday)       temporary access
    admin.unassign("user:ana", "bo", "auditor")
    admin.define_role(RoleDefinition(key="cash.supervisor", tenant="bo", ...))   over a LiveCatalog
    admin.entitle("bo", "e-invoicing")                                  the platform's alone

Context: who acts is the identity in play, and `bounds` (`AdministrationBounds`, enforced by
default) holds them: the system and a superadmin may do anything; anyone else only in their own
tenant, and only give what they hold themselves — a role whose permissions they lack is never
theirs to assign nor to define; what every tenant shares (entitlements, roles of every tenant)
is the platform's. Every change bumps the permission version it touches — the subject's for a
role, the tenant's for an entitlement — so the next call on any replica resolves anew. A role the
catalog does not declare, two roles never held together, two levels of one privilege:
`InvalidGrant` (400), nothing written. Every change written is published when a publisher was
given.
"""

from datetime import datetime

from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.domain.administration import AdministrationBounds
from sincpro_framework.auth.domain.events import SUBJECT, TENANT, EventPublisher
from sincpro_framework.auth.domain.identity import Identity
from sincpro_framework.auth.domain.revocations import Revocations, tenant_subject
from sincpro_framework.auth.grants.adapters.live_catalog import LiveCatalog
from sincpro_framework.auth.grants.domain.assignments import Assignment, GrantStore
from sincpro_framework.auth.grants.domain.catalog import CatalogSource, GrantCatalog
from sincpro_framework.auth.grants.domain.definitions import RoleDefinition
from sincpro_framework.auth.grants.domain.events import (
    ROLE,
    EntitlementWithdrawn,
    LevelChanged,
    RoleAssigned,
    RoleChanged,
    RoleDefined,
    RoleRemoved,
    RoleUnassigned,
    TenantEntitled,
)
from sincpro_framework.auth.infrastructure.announcing import announce
from sincpro_framework.auth.infrastructure.security_context import current_identity


class GrantAdministration:
    """Assignments, entitlements and roles defined at runtime, written once the tenant's catalog
    and `bounds` allow them."""

    def __init__(
        self,
        catalog: CatalogSource,
        store: GrantStore,
        revocations: Revocations | None = None,
        publisher: EventPublisher | None = None,
        bounds: AdministrationBounds = AdministrationBounds(),
    ) -> None:
        self.catalog = catalog
        self.store = store
        self.revocations = revocations
        self.publisher = publisher
        self.bounds = bounds

    def _in_force(self, subject: str, tenant: str | None) -> set[str]:
        """The roles assigned to `subject` in `tenant` and in force now."""
        now = self.store.now()
        return {
            one.role
            for one in self.store.assignments_of(subject, tenant)
            if one.holds_at(now)
        }

    def _allowed(self, catalog: GrantCatalog, roles: set[str]) -> None:
        """`InvalidGrant` saying why `roles` cannot be held together."""
        conflicts = catalog.conflicts(roles)
        if conflicts:
            raise exceptions.InvalidGrant("; ".join(conflicts))

    def _changed(self, subject: str) -> None:
        """Bump the subject's permission version."""
        if self.revocations is not None:
            self.revocations.bump_permissions(subject)

    def _giving(self, actor: Identity, catalog: GrantCatalog, role: str, what: str) -> None:
        """`role` exists in `catalog`, and `actor` holds every permission it gives — what a
        role may be assigned or set as a level by."""
        catalog.role(role)
        self.bounds.holding(actor, catalog.permissions_of({role}), what)

    def assign(
        self, subject: str, tenant: str | None, role: str, valid_until: datetime | None = None
    ) -> None:
        """Give `role` — until `valid_until`, when temporary.

        1. Who acts may administer `tenant`, and holds what `role` gives.
        2. A role the tenant's catalog does not declare: `InvalidGrant`.
        3. Held with what the subject has now, it conflicts: `InvalidGrant`.
        Final: assigned, the subject's version bumped, published.
        """
        actor = current_identity()
        self.bounds.in_scope(actor, tenant, "assign roles")
        catalog = self.catalog.current(tenant)
        self._giving(actor, catalog, role, f"assigning {role}")
        self._allowed(catalog, self._in_force(subject, tenant) | {role})
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
        """Take `role` back — by whoever may administer `tenant`; the subject's version bumped,
        published."""
        self.bounds.in_scope(current_identity(), tenant, "take roles away")
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

        1. Who acts may administer `tenant`, and holds what `role` gives.
        2. A role that is not a level of `privilege`: `InvalidGrant`.
        3. Every other level of it taken back; then `role` given, checked as `assign` checks.
        Final: the subject's version bumped once, published.
        """
        actor = current_identity()
        self.bounds.in_scope(actor, tenant, "set levels")
        catalog = self.catalog.current(tenant)
        levels = [one.key for one in catalog.levels.get(privilege, [])]
        if role is not None and role not in levels:
            raise exceptions.InvalidGrant(f"{role} is not a level of {privilege}")
        kept = self._in_force(subject, tenant) - set(levels)
        if role is not None:
            self._giving(actor, catalog, role, f"setting {role}")
            self._allowed(catalog, kept | {role})
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
        self.bounds.in_scope(current_identity(), None, "entitle tenants")
        if entitlement not in self.catalog.current(tenant).entitlements:
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
        self.bounds.in_scope(current_identity(), None, "withdraw entitlements")
        self.store.withdraw(tenant, entitlement)
        if self.revocations is not None:
            self.revocations.bump_permissions(tenant_subject(tenant))
        announce(
            self.publisher,
            EntitlementWithdrawn(
                entity_type=TENANT, entity_id=tenant, entitlement=entitlement
            ),
        )

    def _live(self) -> LiveCatalog:
        """The `LiveCatalog` given — `ProgrammingError` over a catalog of code alone."""
        if not isinstance(self.catalog, LiveCatalog):
            raise exceptions.ProgrammingError(
                "roles are defined at runtime on a LiveCatalog: "
                "GrantAdministration(LiveCatalog(catalog, definitions), store, ...)"
            )
        return self.catalog

    def define_role(self, definition: RoleDefinition) -> None:
        """Define a role at runtime, new or changed, for its tenant or for every tenant.

        1. Who acts may administer its scope — every tenant's is the platform's.
        2. Who acts holds every permission it would give: its own and what it implies.
        3. Checked as `LiveCatalog.define` checks.
        Final: written, published (`RoleDefined` or `RoleChanged`).
        """
        live = self._live()
        actor = current_identity()
        self.bounds.in_scope(actor, definition.tenant, "define roles")
        catalog = live.current(definition.tenant)
        implied = catalog.permissions_of(
            one for one in definition.implies if one in catalog.roles
        )
        self.bounds.holding(actor, definition.grants | implied, f"defining {definition.key}")
        new = live.define(definition)
        said = RoleDefined if new else RoleChanged
        announce(
            self.publisher,
            said(
                entity_type=ROLE,
                entity_id=definition.key,
                tenant=definition.tenant,
                role_name=definition.name,
                grants=sorted(definition.grants),
                implies=sorted(definition.implies),
                privilege=definition.privilege,
            ),
        )

    def remove_role(self, key: str, tenant: str | None = None) -> None:
        """Remove a role defined at runtime for `tenant` — by whoever may administer it; checked
        as `LiveCatalog.remove` checks, and published."""
        self.bounds.in_scope(current_identity(), tenant, "remove roles")
        self._live().remove(key, tenant)
        announce(self.publisher, RoleRemoved(entity_type=ROLE, entity_id=key, tenant=tenant))
