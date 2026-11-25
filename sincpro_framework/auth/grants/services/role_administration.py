"""`RoleAdministration`: defining and removing roles at runtime — for every tenant (the
platform's) or for one tenant (that tenant's, held by `<key>@<tenant>`).

    roles = RoleAdministration(live, grants, revocations, publisher=audit)
    roles.define(RoleDefinition(key="cash.desk", tenant="bo", implies=frozenset({"billing.clerk"})))
    roles.remove("cash.desk", tenant="bo")

Context: a change is checked and written while the tenant's administration lease is held
(`AdministrationLocks`), against the scope stored now — never a replica's cached catalog — and
written over the version it read. Who acts is bounded by `policy`: in its own tenant only,
making a role only of what it may give (`definable`); changing or removing a role takes it away
from its holders, so only who may give it does. What only adds stays so:

| Never | Why |
|---|---|
| a role of the code redefined | it changes in code, reviewed with the use cases that require it |
| a permission no code declares granted | nothing checks it, so it protects nothing |
| `@` in a key | a tenant's role is named `<key>@<tenant>` by the framework, never by hand |
| a retired key defined again | assignments left behind would come back to life |
| a level (`privilege`) of a tenant's own | a level is every tenant's — the code's or the platform's — so a level added for every tenant never breaks a tenant's role |
| the shape of a held role changed (implies, privilege, level, disjoint) — held directly or through a tenant role implying it | its holders would hold together what `assign` refuses; a role every tenant shares never changes shape |
| a change that leaves another role broken | an administrator would take from other roles' holders what it could not take |
| more than `max_roles` in a scope, or `max_implies` implied by one | composing stays cheap for every tenant of a replica |

A role changed so it may take something away — or saved again as it is, what a retry does —
or removed bumps its scope's permission version — the tenant's, or every tenant's: a token
carrying permissions there is refused, whoever held the role directly or through another. A
tenant's role removed takes its assignments with it; one every tenant shares is retired, its
leftover assignments granting nothing — the resolver drops retired keys. Each step after the
write can be asked again: a removal asked again finishes what a failure left.
"""

from collections.abc import Callable
from typing import Any, Protocol

from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.domain.administration import (
    AdministrationPolicy,
    BoundedAdministration,
)
from sincpro_framework.auth.common.domain.documents import rewritten
from sincpro_framework.auth.common.domain.events import SUBJECT, EventPublisher
from sincpro_framework.auth.common.domain.identity import Identity
from sincpro_framework.auth.common.domain.revocations import (
    EVERY_TENANT,
    Revocations,
    tenant_subject,
)
from sincpro_framework.auth.common.infrastructure.announcing import announce
from sincpro_framework.auth.common.infrastructure.locks import AdministrationLocks
from sincpro_framework.auth.common.infrastructure.security_context import current_identity
from sincpro_framework.auth.grants.domain.assignments import GrantStore
from sincpro_framework.auth.grants.domain.catalog import Composition, GrantCatalog
from sincpro_framework.auth.grants.domain.composition import compose
from sincpro_framework.auth.grants.domain.definitions import (
    TENANT_MARK,
    DefinedRoles,
    RoleDefinition,
    RoleDefinitions,
    catalog_key,
)
from sincpro_framework.auth.grants.domain.events import (
    ROLE,
    RoleChanged,
    RoleDefined,
    RoleRemoved,
    RoleUnassigned,
)
from sincpro_framework.auth.grants.domain.giving import definable


def _shape(definition: RoleDefinition) -> dict[str, Any]:
    """What an event says a role is."""
    return {
        "role_name": definition.name,
        "grants": sorted(definition.grants),
        "implies": sorted(definition.implies),
        "privilege": definition.privilege,
        "sequence": definition.sequence,
        "disjoint": sorted(definition.disjoint),
    }


def _defined(
    definition: RoleDefinition, before: RoleDefinition | None
) -> RoleDefined | RoleChanged:
    """The event of `definition` written over `before` — `RoleChanged` with what it was."""
    said = {
        "entity_type": ROLE,
        "entity_id": definition.catalog_key,
        "tenant": definition.tenant,
    }
    if before is None:
        return RoleDefined(**said, **_shape(definition))
    return RoleChanged(**said, **_shape(definition), previous=_shape(before))


def _takes_away(before: RoleDefinition, after: RoleDefinition) -> bool:
    """Whether `after` may hold less than `before` — fewer grants, or fewer roles implied."""
    return not (before.grants <= after.grants and before.implies <= after.implies)


class RuntimeCatalog(Protocol):
    """What defining roles at runtime asks of the catalog it changes — `LiveCatalog` answers."""

    code: GrantCatalog
    definitions: RoleDefinitions

    @property
    def permissions(self) -> frozenset[str]:
        """What a defined role may grant."""
        ...

    def scopes(self, tenant: str | None) -> tuple[DefinedRoles, DefinedRoles]:
        """What every tenant shares and what `tenant` defines, read now."""
        ...

    def composed(
        self, tenant: str | None, shared: DefinedRoles, own: DefinedRoles
    ) -> Composition:
        """The composition of those documents, as stored — kept by their versions."""
        ...

    def stale(self, tenant: str | None = None) -> None:
        """Read `tenant`'s scopes again on the next call — every tenant's for `None`."""
        ...


class RoleAdministration:
    """Roles defined at runtime, written once `policy` and the catalog allow them."""

    def __init__(
        self,
        catalog: RuntimeCatalog,
        store: GrantStore,
        revocations: Revocations | None = None,
        publisher: EventPublisher | None = None,
        policy: AdministrationPolicy | None = None,
        locks: AdministrationLocks | None = None,
        max_roles: int = 256,
        max_implies: int = 32,
    ) -> None:
        """`locks` — the tenant leases every administration of auth shares; `max_roles` and
        `max_implies` bound what one scope may define, so composing stays cheap."""
        self.catalog = catalog
        self.store = store
        self.revocations = revocations
        self.publisher = publisher
        self.policy = policy or BoundedAdministration()
        self.locks = locks or AdministrationLocks()
        self.max_roles = max_roles
        self.max_implies = max_implies

    def _bump(self, tenant: str | None) -> None:
        """Bump the scope's permission version — the tenant's, or every tenant's: every token
        carrying permissions there is refused, whoever held the role directly or through
        another."""
        if self.revocations is not None:
            self.revocations.bump_permissions(
                EVERY_TENANT if tenant is None else tenant_subject(tenant)
            )

    def _held(self, key: str, tenant: str | None, composition: Composition) -> bool:
        """Whether anyone holds `key` in `tenant` — given it, or a role of the tenant's own that
        implies it. One every tenant shares always counts as held: its holders are in every
        tenant, never listed at once."""
        if tenant is None:
            return True
        catalog = composition.catalog
        through = {one for one in composition.own if key in catalog.closure({one})} | {key}
        return any(self.store.holders(one, tenant) for one in sorted(through))

    def _checked(
        self,
        actor: Identity,
        definition: RoleDefinition,
        shared: DefinedRoles,
        own: DefinedRoles,
    ) -> DefinedRoles:
        """The scope's next document with `definition` — refused when it cannot hold.

        1. `@` in its key, a key retired here or by every tenant, more than the bounds allow.
        2. Changing a role: who acts may take the one stored away — unless it no longer builds;
           a held one (`_held`), or one every tenant shares, keeps its shape.
        3. Who acts may give what the new one is made of.
        Final: the scope with it, composed — refused unless every role that built before, and
        it, still build.
        """
        tenant, key = definition.tenant, definition.key
        scope = own if tenant is not None else shared
        if TENANT_MARK in key:
            raise exceptions.InvalidGrant(f"{key}: `{TENANT_MARK}` is not part of a key")
        if key in scope.retired or key in shared.retired:
            raise exceptions.InvalidGrant(f"{key} was removed: a removed key is never reused")
        if len(definition.implies) > self.max_implies:
            raise exceptions.InvalidGrant(f"{key} implies more than {self.max_implies} roles")
        previous = scope.role(key)
        if previous is None and len(scope.roles) >= self.max_roles:
            raise exceptions.InvalidGrant(f"no more than {self.max_roles} roles in a scope")
        before = self.catalog.composed(tenant, shared, own)
        what = f"defining {definition.catalog_key}"
        if previous is not None:
            if previous.catalog_key not in before.problems:
                definable(self.policy, actor, before, previous, what)
            if not previous.same_shape(definition) and self._held(
                previous.catalog_key, tenant, before
            ):
                raise exceptions.InvalidGrant(
                    f"{definition.catalog_key} is held: what it implies, its privilege, "
                    "level and disjoint roles stay as they are — define a new role instead"
                )
        definable(self.policy, actor, before, definition, what)
        written = scope.replacing(definition)
        after = compose(
            self.catalog.code,
            written if tenant is None else shared,
            written if tenant is not None else own,
            tenant,
        )
        broken = sorted(
            set(after.problems) - (set(before.problems) - {definition.catalog_key})
        )
        if broken:
            raise exceptions.InvalidGrant(
                "; ".join(f"{one}: {after.problems[one]}" for one in broken)
            )
        return written

    def _write(
        self, tenant: str | None, change: Callable[[DefinedRoles, DefinedRoles], DefinedRoles]
    ) -> None:
        """Apply `change` while `tenant`'s lease is held, over the version read — the other
        scope read alongside; every instance of the catalog read again on its next call."""
        with self.locks.held(tenant):
            shared, own = self.catalog.scopes(tenant)
            rewritten(
                self.catalog.definitions,
                tenant,
                lambda current: change(
                    current if tenant is None else shared,
                    current if tenant is not None else own,
                ),
            )
        self.catalog.stale(tenant)

    def define(self, definition: RoleDefinition) -> None:
        """Define a role at runtime, new or changed, in its scope.

        1. Who acts may administer its scope — every tenant's is the platform's.
        2. A permission no code declares, granted, or a tenant's own level: `InvalidGrant`.
        3. Checked and written as `_checked` says, under the tenant's lease.
        4. Published — `RoleDefined`, or `RoleChanged` with what it was; nothing when saved as
           it was.
        Final: changed so it may take something away, or saved again as it was, the scope's
        permission version bumped.
        """
        actor = current_identity()
        self.policy.in_scope(actor, definition.tenant, "define roles")
        unknown = sorted(definition.grants - self.catalog.permissions)
        if unknown:
            raise exceptions.InvalidGrant(
                f"{definition.key} grants {', '.join(unknown)}, which no code declares"
            )
        if definition.tenant is not None and definition.privilege is not None:
            raise exceptions.InvalidGrant(
                f"{definition.key}: a level is every tenant's — the code's or the platform's"
            )
        previous: list[RoleDefinition | None] = [None]

        def defining(shared: DefinedRoles, own: DefinedRoles) -> DefinedRoles:
            scope = own if definition.tenant is not None else shared
            previous[0] = scope.role(definition.key)
            return self._checked(actor, definition, shared, own)

        self._write(definition.tenant, defining)
        before = previous[0]
        if before != definition:
            announce(self.publisher, _defined(definition, before))
        if before is not None and (before == definition or _takes_away(before, definition)):
            self._bump(definition.tenant)

    def _removed(
        self,
        actor: Identity,
        key: str,
        tenant: str | None,
        shared: DefinedRoles,
        own: DefinedRoles,
    ) -> DefinedRoles:
        """The scope's next document without `key`, retired — refused for one not defined
        there, one another role of the scope implies, or one who acts may not take away (unless
        it no longer builds: it grants nothing to take)."""
        scope = own if tenant is not None else shared
        found = scope.role(key)
        if found is None:
            raise exceptions.InvalidGrant(f"{key} is not a role defined for this scope")
        implying = sorted(one.key for one in scope.roles if found.catalog_key in one.implies)
        if implying:
            raise exceptions.InvalidGrant(
                f"{found.catalog_key} is implied by {', '.join(implying)}"
            )
        composition = self.catalog.composed(tenant, shared, own)
        if found.catalog_key not in composition.problems:
            definable(self.policy, actor, composition, found, f"removing {found.catalog_key}")
        return scope.without(key)

    def _retired(self, actor: Identity, key: str, tenant: str | None) -> bool:
        """Retire `key` in its scope, under the tenant's lease — `False` when a removal before
        already did, and only what it left is to finish."""
        with self.locks.held(tenant):
            shared, own = self.catalog.scopes(tenant)
            scope = own if tenant is not None else shared
            if scope.role(key) is None and key in scope.retired:
                return False
            rewritten(
                self.catalog.definitions,
                tenant,
                lambda current: self._removed(
                    actor,
                    key,
                    tenant,
                    current if tenant is None else shared,
                    current if tenant is not None else own,
                ),
            )
        return True

    def remove(self, key: str, tenant: str | None = None) -> None:
        """Remove the role of own key `key` defined for `tenant` — retired.

        1. Who acts may administer the scope; a key of the code: `InvalidGrant`.
        2. Retired as `_removed` says, under the tenant's lease, and published (`RoleRemoved`)
           — or, retired by a removal before, what it left finished.
        3. A tenant's role: taken from every holder, each published (`RoleUnassigned`).
        Final: the scope's permission version bumped — every token carrying permissions there
        refused.
        """
        actor = current_identity()
        self.policy.in_scope(actor, tenant, "remove roles")
        if key in self.catalog.code.roles:
            raise exceptions.InvalidGrant(f"{key}: declared in code, changed there")
        removed = catalog_key(key, tenant)
        if self._retired(actor, key, tenant):
            announce(
                self.publisher,
                RoleRemoved(entity_type=ROLE, entity_id=removed, tenant=tenant),
            )
        self.catalog.stale(tenant)
        if tenant is not None:
            for held in self.store.holders(removed, tenant):
                self.store.unassign(held.subject, tenant, removed)
                announce(
                    self.publisher,
                    RoleUnassigned(
                        entity_type=SUBJECT,
                        entity_id=held.subject,
                        tenant=tenant,
                        role=removed,
                    ),
                )
        self._bump(tenant)
