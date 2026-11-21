"""`RuntimeRequirements`: what an administrator requires of a use case at runtime, beside what its
code declares — for every tenant or for one — asked on every call as an `every_call` check,
without a deploy.

    runtime = auth.require_at_runtime(KeyValueAddedRequirements(), permissions=BillingPermission,
                                      publisher=audit)
    runtime.require(CommandApprovePayment, permissions={BillingPermission.APPROVE})   every tenant
    runtime.require(CommandApprovePayment, tenant="bo", roles={"cash.supervisor"})    tenant bo
    runtime.release(CommandApprovePayment, tenant="bo")

Context: it only restricts. What the code declares holds whatever is required here, so a mistake
on the screen never opens what the code closed; a caller of tenant `bo` meets what every tenant
is required and what `bo` is. A requirement is judged as `requires` judges — the same providers,
the same refusal naming what was missing. Changing them is bounded as every administration is
(`AdministrationPolicy`, `BoundedAdministration` by default): a tenant's administrator for its own
tenant and only with what it holds itself — never requiring, nor releasing, a permission or a
role it lacks, so a weaker administrator never shuts out a stronger one — the platform for every
tenant. A replica reads them at most once each `check_every` (a second) per tenant; the writer's
replica at once. A store that does not answer leaves the last requirements read in effect.
"""

import time
from collections.abc import Callable, Iterable, Mapping
from datetime import timedelta
from typing import Any, Protocol

from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.domain.added_requirements import (
    AddedRequirement,
    AddedRequirements,
    AddedRequirementStore,
)
from sincpro_framework.auth.domain.administration import (
    AdministrationPolicy,
    BoundedAdministration,
)
from sincpro_framework.auth.domain.events import (
    USE_CASE,
    EventPublisher,
    RequirementAdded,
    RequirementRemoved,
)
from sincpro_framework.auth.domain.identity import Identity
from sincpro_framework.auth.domain.permissions import (
    AccessCall,
    AccessDeclaration,
    Permission,
    Requirement,
    RoleRequirement,
    command_name,
)
from sincpro_framework.auth.infrastructure.announcing import announce
from sincpro_framework.auth.infrastructure.refreshed import Refreshed
from sincpro_framework.auth.infrastructure.security_context import current_identity


class Judging(Protocol):
    """What the requirements ask of the `AccessControl` that holds them."""

    def refusal(
        self,
        identity: Identity,
        declaration: AccessDeclaration,
        what: str,
        resource: Any = None,
        context: Mapping[str, Any] | None = None,
        dto: Any = None,
    ) -> exceptions.AuthError | None:
        """Why `identity` may not run what `declaration` guards — `None` when it may."""
        ...

    def guarded_use_cases(self) -> set[str]:
        """The dotted names of every Command of every bus guarded."""
        ...


Scoped = dict[str, list[AddedRequirement]]
"""What holds for a tenant, by Command: every tenant's and its own."""


class RuntimeRequirements:
    """The requirements `store` keeps, judged by `access` on every call."""

    def __init__(
        self,
        access: Judging,
        store: AddedRequirementStore,
        permissions: Iterable[Permission],
        policy: AdministrationPolicy | None = None,
        check_every: timedelta = timedelta(seconds=1),
        clock: Callable[[], float] = time.monotonic,
        publisher: EventPublisher | None = None,
    ) -> None:
        """`permissions` — every member of the project's `Permission` classes — is what may be
        required: a permission no code declares protects nothing."""
        self.access = access
        self.store = store
        self.permissions = {member.value: member for member in permissions}
        self.policy = policy or BoundedAdministration()
        self.publisher = publisher
        self._held: Refreshed[Scoped] = Refreshed(
            read=self._read,
            build=self._scoped,
            empty=((), ()),
            what="the requirements added at runtime",
            check_every=check_every,
            clock=clock,
        )

    def _read(self, tenant: str | None) -> tuple[tuple[int, ...], Any]:
        """The versions and the requirements of every tenant and of `tenant`."""
        shared = self.store.read(None)
        own = AddedRequirements() if tenant is None else self.store.read(tenant)
        return (shared.version, own.version), (shared.requirements, own.requirements)

    @staticmethod
    def _scoped(content: tuple[Iterable[AddedRequirement], ...]) -> Scoped:
        """What every tenant and the tenant require, by Command."""
        by_command: Scoped = {}
        for requirements in content:
            for one in requirements:
                by_command.setdefault(one.command, []).append(one)
        return by_command

    def _requirements(self, added: AddedRequirement) -> list[Requirement]:
        """`added` as `requires` takes it: every permission, and one of the roles."""
        required: list[Requirement] = [
            self.permissions[one] for one in sorted(added.permissions)
        ]
        if added.roles:
            required.append(RoleRequirement(tuple(sorted(added.roles))))
        return required

    def check(self, call: AccessCall) -> bool:
        """The `every_call` check: what is required of `call.command` for the caller's tenant,
        judged as `requires` judges — its refusal raised, so it names what was missing."""
        added = self._held.get(call.identity.tenant).get(call.command, [])
        if not added:
            return True
        required = [one for each in added for one in self._requirements(each)]
        refused = self.access.refusal(
            call.identity,
            AccessDeclaration("requires", tuple(required)),
            call.use_case,
            call.resource,
            call.context,
            call.dto,
        )
        if refused is not None:
            raise refused
        return True

    def required(self, tenant: str | None = None) -> tuple[AddedRequirement, ...]:
        """The requirements added for exactly this scope — what a screen lists."""
        return self.store.read(tenant).requirements

    def _write(
        self, tenant: str | None, change: Callable[[AddedRequirements], AddedRequirements]
    ) -> None:
        """Apply `change` to the scope read now and write it over that version — read again and
        asked again while another administrator wrote first; this replica current at once."""
        for _ in range(5):
            added = self.store.read(tenant)
            if self.store.write(tenant, change(added)):
                self._held.stale(tenant, everything=tenant is None)
                return
        raise exceptions.InvalidGrant("the requirements changed under this write: try again")

    def _holding(self, actor: Identity, added: AddedRequirement | None, what: str) -> None:
        """`actor` holds every permission and role `added` names — what it may require or
        release."""
        if added is not None:
            self.policy.holding(actor, what, permissions=added.permissions, roles=added.roles)

    def require(
        self,
        command: type | str,
        permissions: Iterable[str] = (),
        roles: Iterable[str] = (),
        tenant: str | None = None,
    ) -> AddedRequirement:
        """From now on `command`'s use case also requires every one of `permissions` and, when
        any is named, one of `roles` — for `tenant`, or every tenant — replacing what was
        required of it in that scope before.

        1. Who acts may administer the scope — every tenant's is the platform's.
        2. A use case no guarded bus answers, a permission no code declares, or nothing named:
           `InvalidGrant`.
        3. Inside the write: who acts holds what it requires, and what it replaces.
        Final: written over the version read, and published (`RequirementAdded`).
        """
        name = command if isinstance(command, str) else command_name(command)
        actor = current_identity()
        what = f"requiring more of {name}"
        self.policy.in_scope(actor, tenant, what)
        wanted = AddedRequirement(
            command=name,
            tenant=tenant,
            permissions=frozenset(str(one) for one in permissions),
            roles=frozenset(roles),
        )
        if name not in self.access.guarded_use_cases():
            raise exceptions.InvalidGrant(f"{name} is not a use case of a bus guarded")
        unknown = sorted(wanted.permissions - self.permissions.keys())
        if unknown:
            raise exceptions.InvalidGrant(f"{', '.join(unknown)}: no code declares it")
        if not wanted.permissions and not wanted.roles:
            raise exceptions.InvalidGrant(f"{name}: name a permission or a role to require")

        def requiring(added: AddedRequirements) -> AddedRequirements:
            self._holding(actor, added.of(name), what)
            self._holding(actor, wanted, what)
            return added.replacing(wanted)

        self._write(tenant, requiring)
        announce(
            self.publisher,
            RequirementAdded(
                entity_type=USE_CASE,
                entity_id=name,
                tenant=tenant,
                permissions=sorted(wanted.permissions),
                roles=sorted(wanted.roles),
            ),
        )
        return wanted

    def release(self, command: type | str, tenant: str | None = None) -> None:
        """Nothing required of `command`'s use case at runtime in that scope any more — by
        whoever may administer it and holds what was required; published (`RequirementRemoved`).
        Nothing when nothing was required."""
        name = command if isinstance(command, str) else command_name(command)
        actor = current_identity()
        what = f"releasing {name}"
        self.policy.in_scope(actor, tenant, what)
        released = [False]

        def releasing(added: AddedRequirements) -> AddedRequirements:
            before = added.of(name)
            self._holding(actor, before, what)
            released[0] = before is not None
            return added.without(name)

        if self.store.read(tenant).of(name) is None:
            return
        self._write(tenant, releasing)
        if released[0]:
            announce(
                self.publisher,
                RequirementRemoved(entity_type=USE_CASE, entity_id=name, tenant=tenant),
            )
