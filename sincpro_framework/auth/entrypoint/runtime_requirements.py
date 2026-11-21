"""`RuntimeRequirements`: what an administrator requires of a use case at runtime, beside what its
code declares — for every tenant or for one — asked on every call as an `every_call` check,
without a deploy.

    auth = AccessControl[BillingPermission](providers=jwt, runtime=True)
    auth.runtime.require(CommandApprovePayment, BillingPermission.APPROVE)         every tenant
    auth.runtime.require(CommandApprovePayment, auth.role("cash.lead@bo"), tenant="bo")
    auth.runtime.release(CommandApprovePayment, tenant="bo")

Context: it only restricts. What the code declares holds whatever is required here, so a mistake
on the screen never opens what the code closed; a caller of tenant `bo` meets what every tenant
is required and what `bo` is. A requirement is judged as `requires` judges — the same providers,
the same refusal naming what was missing; a permission this replica does not know — a newer
replica's, a renamed one — is never met. Changing them is bounded as every administration is
(`AdministrationPolicy`, `BoundedAdministration` by default) and done under the tenant's
administration lease: the platform for every tenant; a tenant's administrator for its own, and
only requiring, replacing or releasing what it holds itself. A requirement still refuses every
administrator who lacks it — whoever holds it, or the platform, releases it. Read at most once
each `check_every` per tenant, one caller each; the writer's instance at once. A store that does
not answer leaves the last requirements read in effect — and a tenant never read is refused (503)
until it answers: what only restricts fails closed.
"""

import copy
import time
from collections.abc import Callable, Hashable, Iterable, Mapping
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
from sincpro_framework.auth.domain.documents import fingerprint, rewritten
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
    Rule,
    command_name,
    declared_permissions,
)
from sincpro_framework.auth.infrastructure.announcing import announce
from sincpro_framework.auth.infrastructure.locks import AdministrationLocks
from sincpro_framework.auth.infrastructure.refreshed import Refreshed
from sincpro_framework.auth.infrastructure.security_context import current_identity
from sincpro_framework.sincpro_logger import logger


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


def _wanted(
    command: str, tenant: str | None, required: Iterable[str | RoleRequirement]
) -> AddedRequirement:
    """What `require` was given, as it is kept: the permissions by value, the roles of the one
    `auth.role(...)` — any other requirement is the code's to declare."""
    roles = [one for one in required if isinstance(one, RoleRequirement)]
    if len(roles) > 1:
        raise exceptions.InvalidGrant(
            f"{command}: name the roles in one auth.role(...) — any of them meets it"
        )
    others = [one for one in required if not isinstance(one, (str, RoleRequirement))]
    if others:
        raise exceptions.InvalidGrant(
            f"{command}: only permissions and auth.role(...) are required at runtime"
        )
    return AddedRequirement(
        command=command,
        tenant=tenant,
        permissions=frozenset(str(one) for one in required if isinstance(one, str)),
        roles=frozenset(roles[0].roles) if roles else frozenset(),
    )


def _unknown(value: str) -> Rule:
    """A requirement never met: a permission this replica's code does not declare."""
    return Rule(f"{value}, which this service does not know", lambda call: False)


class RuntimeRequirements:
    """The requirements `store` keeps, judged by `access` on every call."""

    def __init__(
        self,
        access: Judging,
        store: AddedRequirementStore,
        permissions: Iterable[Permission] | None = None,
        policy: AdministrationPolicy | None = None,
        locks: AdministrationLocks | None = None,
        check_every: timedelta = timedelta(seconds=1),
        clock: Callable[[], float] = time.monotonic,
        publisher: EventPublisher | None = None,
    ) -> None:
        """`permissions` is what may be required — every `Permission` the code declares when not
        given, read when asked: a permission no code declares protects nothing."""
        self.access = access
        self.store = store
        self._permissions = (
            None if permissions is None else {member.value: member for member in permissions}
        )
        self.policy = policy or BoundedAdministration()
        self.locks = locks or AdministrationLocks()
        self.publisher = publisher
        self._held: Refreshed[Scoped] = Refreshed(
            read=self._read,
            build=self._scoped,
            what="the requirements added at runtime",
            check_every=check_every,
            clock=clock,
        )

    def judged_by(self, access: Judging) -> "RuntimeRequirements":
        """These requirements — the same store, bound, leases and what was read — judged by
        `access`: what `AccessControl.using` gives the copy it makes."""
        judged = copy.copy(self)
        judged.access = access
        return judged

    @property
    def permissions(self) -> dict[str, Permission]:
        """What may be required, by value."""
        return declared_permissions() if self._permissions is None else self._permissions

    def _read(self, tenant: str | None) -> tuple[tuple[Hashable, ...], Any]:
        """The fingerprint and the requirements of every tenant and of `tenant`."""
        shared = self.store.read(None)
        own = AddedRequirements() if tenant is None else self.store.read(tenant)
        return fingerprint(shared, own), (shared.requirements, own.requirements)

    @staticmethod
    def _scoped(content: tuple[Iterable[AddedRequirement], ...]) -> Scoped:
        """What every tenant and the tenant require, by Command."""
        by_command: Scoped = {}
        for requirements in content:
            for one in requirements:
                by_command.setdefault(one.command, []).append(one)
        return by_command

    def _requirements(self, added: AddedRequirement) -> list[Requirement]:
        """`added` as `requires` takes it: every permission — one this replica does not know
        never met, and logged — and one of the roles."""
        required: list[Requirement] = []
        known = self.permissions
        for value in sorted(added.permissions):
            member = known.get(value)
            if member is None:
                logger.warning(
                    f"{added.command} requires {value}, which this service does not know"
                )
                required.append(_unknown(value))
            else:
                required.append(member)
        if added.roles:
            required.append(RoleRequirement(tuple(sorted(added.roles))))
        return required

    def check(self, call: AccessCall) -> bool:
        """The `every_call` check: what is required of `call.command` for the caller's tenant,
        judged as `requires` judges — its refusal raised, so it names what was missing; the
        store's error raised for a tenant never read while it does not answer."""
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

    def _holding(self, actor: Identity, added: AddedRequirement | None, what: str) -> None:
        """`actor` holds every permission and role `added` names — what it may require or
        release."""
        if added is not None:
            self.policy.holding(actor, what, permissions=added.permissions, roles=added.roles)

    def _write(
        self, tenant: str | None, change: Callable[[AddedRequirements], AddedRequirements]
    ) -> None:
        """Apply `change` under the tenant's lease, over the version read; this instance reads
        again on its next call."""
        with self.locks.held(tenant):
            rewritten(self.store, tenant, change)
        self._held.stale(tenant, everything=tenant is None)

    def require(
        self,
        command: type | str,
        *required: str | RoleRequirement,
        tenant: str | None = None,
    ) -> AddedRequirement:
        """From now on `command`'s use case also requires every permission named and, with an
        `auth.role(...)`, one of its roles — for `tenant`, or every tenant — replacing what was
        required of it in that scope before. A permission is a `Permission` member or its value,
        as a screen sends it.

            auth.runtime.require(CommandRefund, BillingPermission.APPROVE, tenant="bo")
            auth.runtime.require(CommandRefund, auth.role("cash.lead@bo"), tenant="bo")

        1. Who acts may administer the scope — every tenant's is the platform's.
        2. A use case no guarded bus answers, a permission no code declares, more than one
           `auth.role(...)`, or nothing named: `InvalidGrant`.
        3. Under the tenant's lease: who acts holds what it requires, and what it replaces.
        Final: written over the version read, and published (`RequirementAdded`).
        """
        name = command if isinstance(command, str) else command_name(command)
        actor = current_identity()
        what = f"requiring more of {name}"
        self.policy.in_scope(actor, tenant, what)
        wanted = _wanted(name, tenant, required)
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
