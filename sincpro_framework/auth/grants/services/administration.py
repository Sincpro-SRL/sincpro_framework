"""`GrantAdministration`: giving and taking roles and entitlements, checked against the catalog —
what an administration screen or a use case of the project calls.

    admin = GrantAdministration(catalog, grants, revocations)
    admin.set_level("user:ana", "bo", "invoicing", "billing.manager")   one level per privilege
    admin.assign("user:ana", "bo", "auditor", valid_until=friday)       temporary access
    admin.unassign("user:ana", "bo", "auditor")
    admin.entitle("bo", "e-invoicing")

Context: every change bumps the permission version it touches — the subject's for a role, the
tenant's for an entitlement — so the next call on any replica resolves anew. A role the catalog
does not declare, two roles never held together, two levels of one privilege: `InvalidGrant`
(400), nothing written.
"""

from datetime import datetime

from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.domain.revocations import Revocations, tenant_subject
from sincpro_framework.auth.grants.domain.assignments import Assignment, GrantStore
from sincpro_framework.auth.grants.domain.catalog import GrantCatalog


class GrantAdministration:
    """Assignments and entitlements, written to `store` once the catalog allows them."""

    def __init__(
        self, catalog: GrantCatalog, store: GrantStore, revocations: Revocations | None = None
    ) -> None:
        self.catalog = catalog
        self.store = store
        self.revocations = revocations

    def _in_force(self, subject: str, tenant: str | None) -> set[str]:
        """The roles assigned to `subject` in `tenant` and in force now."""
        now = self.store.now()
        return {
            one.role
            for one in self.store.assignments_of(subject, tenant)
            if one.holds_at(now)
        }

    def _allowed(self, roles: set[str]) -> None:
        """`InvalidGrant` saying why `roles` cannot be held together."""
        conflicts = self.catalog.conflicts(roles)
        if conflicts:
            raise exceptions.InvalidGrant("; ".join(conflicts))

    def _changed(self, subject: str) -> None:
        """Bump the subject's permission version."""
        if self.revocations is not None:
            self.revocations.bump_permissions(subject)

    def assign(
        self, subject: str, tenant: str | None, role: str, valid_until: datetime | None = None
    ) -> None:
        """Give `role` — until `valid_until`, when temporary.

        1. A role the catalog does not declare: `InvalidGrant`.
        2. Held with what the subject has now, it conflicts: `InvalidGrant`.
        Final: assigned, the subject's version bumped.
        """
        self.catalog.role(role)
        self._allowed(self._in_force(subject, tenant) | {role})
        self.store.assign(
            Assignment(subject=subject, tenant=tenant, role=role, valid_until=valid_until)
        )
        self._changed(subject)

    def unassign(self, subject: str, tenant: str | None, role: str) -> None:
        """Take `role` back; the subject's version bumped."""
        self.store.unassign(subject, tenant, role)
        self._changed(subject)

    def set_level(
        self, subject: str, tenant: str | None, privilege: str, role: str | None
    ) -> None:
        """Hold `role` as the one level of `privilege` — `None` for no access.

        1. A role that is not a level of `privilege`: `InvalidGrant`.
        2. Every other level of it taken back; then `role` given, checked as `assign` checks.
        Final: the subject's version bumped once.
        """
        levels = [one.key for one in self.catalog.levels.get(privilege, [])]
        if role is not None and role not in levels:
            raise exceptions.InvalidGrant(f"{role} is not a level of {privilege}")
        kept = self._in_force(subject, tenant) - set(levels)
        if role is not None:
            self._allowed(kept | {role})
        for level in levels:
            if level != role:
                self.store.unassign(subject, tenant, level)
        if role is not None:
            self.store.assign(Assignment(subject=subject, tenant=tenant, role=role))
        self._changed(subject)

    def entitle(self, tenant: str, entitlement: str) -> None:
        """The tenant has `entitlement`; its version bumped."""
        if entitlement not in self.catalog.entitlements:
            raise exceptions.InvalidGrant(f"{entitlement} is not an entitlement")
        self.store.entitle(tenant, entitlement)
        if self.revocations is not None:
            self.revocations.bump_permissions(tenant_subject(tenant))

    def withdraw(self, tenant: str, entitlement: str) -> None:
        """The tenant no longer has `entitlement`; its version bumped."""
        self.store.withdraw(tenant, entitlement)
        if self.revocations is not None:
            self.revocations.bump_permissions(tenant_subject(tenant))
