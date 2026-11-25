"""`RoleAdministration`: defining and removing roles at runtime — for every tenant (the
platform's) or for one tenant (that tenant's, held by `<key>@<tenant>`).

    roles = RoleAdministration(tenant_catalogs, grants, revocations, publisher=audit)
    roles.define(RoleDefinition(key="cash.desk", tenant="bo", implies=frozenset({"billing.clerk"})))
    roles.remove("cash.desk", tenant="bo")

Context: a change is checked and written while the tenant's administration lease is held
(`AdministrationLocks`), against the scope stored now — never a replica's cached catalog — and
written over the version it read. The project's own rules (`ChangeRules`) are checked first; who
acts is bounded by `policy`: in its own tenant only, making a role only of what it may assign
(`AssignmentAuthority.check_definition`); changing or removing a role takes it away from its
holders, so only who may assign it does. What only adds stays so:

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

from sincpro_framework.auth.common.adapters.locks import AdministrationLocks
from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.domain.administration import (
    AdministrationPolicy,
    BoundedAdministration,
)
from sincpro_framework.auth.common.domain.documents import update_document
from sincpro_framework.auth.common.domain.events import SUBJECT, EventPublisher
from sincpro_framework.auth.common.domain.identity import Identity
from sincpro_framework.auth.common.domain.revocations import (
    EVERY_TENANT,
    Revocations,
    tenant_subject,
)
from sincpro_framework.auth.common.infrastructure.event_publishing import publish_event
from sincpro_framework.auth.common.infrastructure.security_context import current_identity
from sincpro_framework.auth.grants.domain.assignment_authority import AssignmentAuthority
from sincpro_framework.auth.grants.domain.assignments import GrantStore
from sincpro_framework.auth.grants.domain.catalog import EffectiveCatalog, RuntimeCatalog
from sincpro_framework.auth.grants.domain.catalog_builder import CatalogBuilder
from sincpro_framework.auth.grants.domain.change_rules import (
    ChangeRules,
    GrantAction,
    GrantChange,
)
from sincpro_framework.auth.grants.domain.definitions import (
    TENANT_SEPARATOR,
    DefinedRoles,
    RoleDefinition,
    catalog_key,
)
from sincpro_framework.auth.grants.domain.events import (
    ROLE,
    RoleChanged,
    RoleDefined,
    RoleRemoved,
    RoleUnassigned,
)


def _definition_event(
    definition: RoleDefinition, before: RoleDefinition | None
) -> RoleDefined | RoleChanged:
    """The event of `definition` written over `before` — `RoleChanged` with what it was."""
    said = {
        "entity_type": ROLE,
        "entity_id": definition.catalog_key,
        "tenant": definition.tenant,
    }
    if before is None:
        return RoleDefined(**said, **definition.event_fields())
    return RoleChanged(**said, **definition.event_fields(), previous=before.event_fields())


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
        rules: ChangeRules | None = None,
    ) -> None:
        """`locks` — the tenant leases every administration of auth shares; `max_roles` and
        `max_implies` bound what one scope may define, so composing stays cheap; `rules` — the
        project's own, checked against every change."""
        self.catalog = catalog
        self.builder = CatalogBuilder(catalog.code)
        self.store = store
        self.revocations = revocations
        self.publisher = publisher
        self.policy = policy or BoundedAdministration()
        self.authority = AssignmentAuthority(self.policy)
        self.locks = locks or AdministrationLocks()
        self.max_roles = max_roles
        self.max_implies = max_implies
        self.rules = rules or ChangeRules()

    def _bump(self, tenant: str | None) -> None:
        """Bump the scope's permission version — the tenant's, or every tenant's: every token
        carrying permissions there is refused, whoever held the role directly or through
        another."""
        if self.revocations is not None:
            self.revocations.bump_permissions(
                EVERY_TENANT if tenant is None else tenant_subject(tenant)
            )

    def _is_held(self, key: str, tenant: str | None, effective: EffectiveCatalog) -> bool:
        """Whether anyone holds `key` in `tenant` — assigned it, or a role of the tenant's own
        that implies it. One every tenant shares always counts as held: its holders are in every
        tenant, never listed at once."""
        if tenant is None:
            return True
        catalog = effective.catalog
        through = {one for one in effective.own if key in catalog.closure({one})} | {key}
        return any(self.store.holders(one, tenant) for one in sorted(through))

    def _validate_definition(
        self,
        actor: Identity,
        definition: RoleDefinition,
        shared: DefinedRoles,
        own: DefinedRoles,
    ) -> DefinedRoles:
        """The scope's next document with `definition` — refused when it cannot hold.

        1. `@` in its key, a key retired here or by every tenant, more than the bounds allow.
        2. Changing a role: who acts may take the one stored away — unless it no longer builds;
           a held one (`_is_held`), or one every tenant shares, retains its shape.
        3. Who acts may assign what the new one is made of.
        Final: the scope with it, composed — refused unless every role that built before, and
        it, still build.
        """
        tenant, key = definition.tenant, definition.key
        scope = own if tenant is not None else shared
        if TENANT_SEPARATOR in key:
            raise exceptions.InvalidGrant(f"{key}: `{TENANT_SEPARATOR}` is not part of a key")
        if key in scope.retired or key in shared.retired:
            raise exceptions.InvalidGrant(f"{key} was removed: a removed key is never reused")
        if len(definition.implies) > self.max_implies:
            raise exceptions.InvalidGrant(f"{key} implies more than {self.max_implies} roles")
        previous = scope.role(key)
        if previous is None and len(scope.roles) >= self.max_roles:
            raise exceptions.InvalidGrant(f"no more than {self.max_roles} roles in a scope")
        before = self.catalog.compose(tenant, shared, own)
        what = f"defining {definition.catalog_key}"
        if previous is not None:
            if previous.catalog_key not in before.problems:
                self.authority.check_definition(actor, before, previous, what)
            if not previous.same_shape(definition) and self._is_held(
                previous.catalog_key, tenant, before
            ):
                raise exceptions.InvalidGrant(
                    f"{definition.catalog_key} is held: what it implies, its privilege, "
                    "level and disjoint roles stay as they are — define a new role instead"
                )
        self.authority.check_definition(actor, before, definition, what)
        written = scope.with_role(definition)
        after = self.builder.compose(
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
    ) -> DefinedRoles:
        """Apply `change` while `tenant`'s lease is held, over the version read — the other
        scope read alongside; every instance of the catalog read again on its next call.
        Answers the scope as it was before the change."""
        with self.locks.acquire(tenant):
            shared, own = self.catalog.load_scopes(tenant)
            update_document(
                self.catalog.definitions,
                tenant,
                lambda current: change(
                    current if tenant is None else shared,
                    current if tenant is not None else own,
                ),
            )
        self.catalog.invalidate(tenant)
        return own if tenant is not None else shared

    def define(self, definition: RoleDefinition) -> RoleDefinition:
        """Define a role at runtime, new or changed, in its scope — answers it as defined, the
        roles it names resolved to their catalog keys.

        1. The roles it implies or is disjoint from named as `assign` names them — exactly, else
           the tenant's own of that name; the project's rules allow it; who acts may administer
           its scope — every tenant's is the platform's.
        2. A permission no code declares, granted, or a tenant's own level: `InvalidGrant`.
        3. Checked and written as `_validate_definition` says, under the tenant's lease.
        4. Published — `RoleDefined`, or `RoleChanged` with what it was; nothing when saved as
           it was.
        Final: changed so it may take something away, or saved again as it was, the scope's
        permission version bumped.
        """
        actor = current_identity()
        tenant = definition.tenant
        effective = self.catalog.load(tenant)
        definition = definition.model_copy(
            update={
                "implies": frozenset(
                    effective.resolve_key(one, tenant) for one in definition.implies
                ),
                "disjoint": frozenset(
                    effective.resolve_key(one, tenant) for one in definition.disjoint
                ),
            }
        )
        self.rules.check(
            GrantChange(
                actor,
                GrantAction.DEFINE,
                tenant,
                role=definition.catalog_key,
                definition=definition,
            )
        )
        self.policy.validate_tenant(actor, tenant, "define roles")
        unknown = sorted(definition.grants - self.catalog.permissions)
        if unknown:
            raise exceptions.InvalidGrant(
                f"{definition.key} grants {', '.join(unknown)}, which no code declares"
            )
        if tenant is not None and definition.privilege is not None:
            raise exceptions.InvalidGrant(
                f"{definition.key}: a level is every tenant's — the code's or the platform's"
            )
        scope = self._write(
            tenant,
            lambda shared, own: self._validate_definition(actor, definition, shared, own),
        )
        before = scope.role(definition.key)
        if before != definition:
            publish_event(self.publisher, _definition_event(definition, before))
        if before is not None and (
            before == definition or definition.is_narrower_than(before)
        ):
            self._bump(tenant)
        return definition

    def _validate_removal(
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
        effective = self.catalog.compose(tenant, shared, own)
        if found.catalog_key not in effective.problems:
            self.authority.check_definition(
                actor, effective, found, f"removing {found.catalog_key}"
            )
        return scope.without_role(key)

    def _retire(self, actor: Identity, key: str, tenant: str | None) -> bool:
        """Retire `key` in its scope, under the tenant's lease — `False` when a removal before
        already did, and only what it left is to finish."""
        with self.locks.acquire(tenant):
            shared, own = self.catalog.load_scopes(tenant)
            scope = own if tenant is not None else shared
            if scope.role(key) is None and key in scope.retired:
                return False
            update_document(
                self.catalog.definitions,
                tenant,
                lambda current: self._validate_removal(
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

        1. The project's rules allow it; who acts may administer the scope; a key of the code:
           `InvalidGrant`.
        2. Retired as `_validate_removal` says, under the tenant's lease, and published
           (`RoleRemoved`) — or, retired by a removal before, what it left finished.
        3. A tenant's role: taken from every holder, each published (`RoleUnassigned`).
        Final: the scope's permission version bumped — every token carrying permissions there
        refused.
        """
        actor = current_identity()
        removed = catalog_key(key, tenant)
        self.rules.check(GrantChange(actor, GrantAction.REMOVE, tenant, role=removed))
        self.policy.validate_tenant(actor, tenant, "remove roles")
        if key in self.catalog.code.roles:
            raise exceptions.InvalidGrant(f"{key}: declared in code, changed there")
        if self._retire(actor, key, tenant):
            publish_event(
                self.publisher,
                RoleRemoved(entity_type=ROLE, entity_id=removed, tenant=tenant),
            )
        self.catalog.invalidate(tenant)
        if tenant is not None:
            for holder in self.store.holders(removed, tenant):
                self.store.unassign(holder.subject, tenant, removed)
                publish_event(
                    self.publisher,
                    RoleUnassigned(
                        entity_type=SUBJECT,
                        entity_id=holder.subject,
                        tenant=tenant,
                        role=removed,
                    ),
                )
        self._bump(tenant)
