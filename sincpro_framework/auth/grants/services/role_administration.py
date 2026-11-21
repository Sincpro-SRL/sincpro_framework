"""`RoleAdministration`: defining and removing roles at runtime — for every tenant (the
platform's) or for one tenant (that tenant's) — over a `LiveCatalog`.

    roles = RoleAdministration(live, grants, revocations, publisher=audit)
    roles.define(RoleDefinition(key="cash.supervisor", tenant="bo", implies=frozenset({"billing.clerk"})))
    roles.remove("cash.supervisor", tenant="bo")

Context: every change is checked inside the write, against the scope read at that moment, so a
replica's cached catalog never decides it; two administrators racing over one version never lose
each other's change. Who acts is bounded by `policy`: in its own tenant only, only implying what
it may give (`givable`) and granting what it holds; changing or removing a role is taking it
away from its holders, so only who may give it does. What only adds stays so:

| Never | Why |
|---|---|
| a role of the code redefined | it changes in code, reviewed with the use cases that require it |
| a permission no code declares granted | nothing checks it, so it protects nothing |
| a tenant's role named like one every tenant shares | the tenant's holders would get what nobody assigned them |
| a retired key defined again | assignments left behind would come back to life |
| the shape of an assigned role changed (implies, privilege, level, disjoint) | its holders would hold together what `assign` refuses; every tenant's role never changes shape |

A tenant's role removed takes its assignments with it; one every tenant shares is retired, its
assignments granting nothing, and a tenant role implying it is listed in `problems(tenant)`.
"""

from collections.abc import Callable

from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.domain.administration import (
    AdministrationPolicy,
    BoundedAdministration,
)
from sincpro_framework.auth.domain.events import SUBJECT, EventPublisher
from sincpro_framework.auth.domain.identity import Identity
from sincpro_framework.auth.domain.revocations import Revocations
from sincpro_framework.auth.grants.adapters.live_catalog import LiveCatalog
from sincpro_framework.auth.grants.domain.assignments import GrantStore
from sincpro_framework.auth.grants.domain.composition import compose
from sincpro_framework.auth.grants.domain.definitions import DefinedRoles, RoleDefinition
from sincpro_framework.auth.grants.domain.events import (
    ROLE,
    RoleChanged,
    RoleDefined,
    RoleRemoved,
    RoleUnassigned,
)
from sincpro_framework.auth.grants.domain.giving import givable
from sincpro_framework.auth.infrastructure.announcing import announce
from sincpro_framework.auth.infrastructure.security_context import current_identity

Change = Callable[[DefinedRoles, DefinedRoles], DefinedRoles]
"""What a write does to a scope: given every tenant's document and the tenant's own, the scope's
next document — raising when the change cannot hold."""


class RoleAdministration:
    """Roles defined at runtime, written once `policy` and the catalog allow them."""

    def __init__(
        self,
        live: LiveCatalog,
        store: GrantStore,
        revocations: Revocations | None = None,
        publisher: EventPublisher | None = None,
        policy: AdministrationPolicy | None = None,
    ) -> None:
        self.live = live
        self.store = store
        self.revocations = revocations
        self.publisher = publisher
        self.policy = policy or BoundedAdministration()

    def _write(self, tenant: str | None, change: Change) -> DefinedRoles:
        """Apply `change` to the scope read now and write it over that version — read again and
        asked again while another administrator wrote first; every replica's next read sees it,
        this one at once."""
        for _ in range(5):
            shared, own = self.live.scopes(tenant)
            document = change(shared, own)
            if self.live.definitions.write(tenant, document):
                self.live.stale(tenant)
                return document
        raise exceptions.InvalidGrant(
            "the roles changed under this write five times: try again"
        )

    def _assigned(self, key: str, tenant: str | None) -> bool:
        """Whether anybody holds `key` — every tenant's role is taken as held: its holders are
        in every tenant, never listed at once."""
        return tenant is None or bool(self.store.holders(key, tenant))

    def _defining(
        self,
        actor: Identity,
        definition: RoleDefinition,
        shared: DefinedRoles,
        own: DefinedRoles,
    ) -> DefinedRoles:
        """The scope's next document with `definition` — refused when it cannot hold.

        1. A key retired here or by every tenant, or a tenant's key every tenant shares: refused.
        2. Changing a role: who acts may take it away; an assigned one keeps its shape.
        3. Who acts may give every role it implies, and holds every permission it grants.
        Final: the scope with it, composed — refused unless it builds.
        """
        tenant, key = definition.tenant, definition.key
        scope = own if tenant is not None else shared
        if key in scope.retired or key in shared.retired:
            raise exceptions.InvalidGrant(f"{key} was removed: a removed key is never reused")
        if tenant is not None and shared.role(key) is not None:
            raise exceptions.InvalidGrant(
                f"{key} is a role every tenant shares: name this one otherwise"
            )
        composition = compose(self.live.code, shared, own)
        previous = scope.role(key)
        if previous is not None:
            givable(self.policy, actor, composition, key, f"changing {key}")
            if not previous.same_shape(definition) and self._assigned(key, tenant):
                raise exceptions.InvalidGrant(
                    f"{key} is assigned: what it implies, its privilege, level and disjoint "
                    "roles stay as they are — define a new role instead"
                )
        for implied in sorted(definition.implies):
            givable(self.policy, actor, composition, implied, f"defining {key}")
        self.policy.holding(actor, f"defining {key}", permissions=definition.grants)
        written = scope.replacing(definition)
        after = compose(
            self.live.code,
            written if tenant is None else shared,
            written if tenant is not None else own,
        )
        if key in after.problems:
            raise exceptions.InvalidGrant(after.problems[key])
        return written

    def define(self, definition: RoleDefinition) -> None:
        """Define a role at runtime, new or changed, in its scope.

        1. Who acts may administer its scope — every tenant's is the platform's.
        2. A permission no code declares, granted: `InvalidGrant`.
        3. Checked and written as `_defining` says, inside the write.
        Final: published — `RoleDefined`, or `RoleChanged` for one defined before.
        """
        actor = current_identity()
        self.policy.in_scope(actor, definition.tenant, "define roles")
        unknown = sorted(definition.grants - self.live.permissions)
        if unknown:
            raise exceptions.InvalidGrant(
                f"{definition.key} grants {', '.join(unknown)}, which no code declares"
            )
        existed = [False]

        def defining(shared: DefinedRoles, own: DefinedRoles) -> DefinedRoles:
            scope = own if definition.tenant is not None else shared
            existed[0] = scope.role(definition.key) is not None
            return self._defining(actor, definition, shared, own)

        self._write(definition.tenant, defining)
        said = RoleChanged if existed[0] else RoleDefined
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

    def _removing(
        self,
        actor: Identity,
        key: str,
        tenant: str | None,
        shared: DefinedRoles,
        own: DefinedRoles,
    ) -> DefinedRoles:
        """The scope's next document without `key`, retired — refused for one not defined
        there, one another role of the scope implies, or one who acts may not take away."""
        scope = own if tenant is not None else shared
        if key in self.live.code.roles:
            raise exceptions.InvalidGrant(f"{key}: declared in code, changed there")
        if scope.role(key) is None:
            raise exceptions.InvalidGrant(f"{key} is not a role defined for this scope")
        implying = sorted(one.key for one in scope.roles if key in one.implies)
        if implying:
            raise exceptions.InvalidGrant(f"{key} is implied by {', '.join(implying)}")
        givable(
            self.policy, actor, compose(self.live.code, shared, own), key, f"removing {key}"
        )
        return scope.without(key)

    def _unassign_all(self, key: str, tenant: str) -> None:
        """Take the removed tenant role from every holder of it — each bumped and published."""
        for held in self.store.holders(key, tenant):
            self.store.unassign(held.subject, tenant, key)
            if self.revocations is not None:
                self.revocations.bump_permissions(held.subject)
            announce(
                self.publisher,
                RoleUnassigned(
                    entity_type=SUBJECT, entity_id=held.subject, tenant=tenant, role=key
                ),
            )

    def remove(self, key: str, tenant: str | None = None) -> None:
        """Remove a role defined at runtime for `tenant` — its key retired.

        1. Who acts may administer the scope, and may take the role away.
        2. Checked and written as `_removing` says, inside the write.
        3. A tenant's role: taken from every holder.
        Final: published — `RoleRemoved`.
        """
        actor = current_identity()
        self.policy.in_scope(actor, tenant, "remove roles")
        self._write(
            tenant, lambda shared, own: self._removing(actor, key, tenant, shared, own)
        )
        if tenant is not None:
            self._unassign_all(key, tenant)
        announce(self.publisher, RoleRemoved(entity_type=ROLE, entity_id=key, tenant=tenant))
