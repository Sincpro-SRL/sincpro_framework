"""`RuntimeRequirements`: what an administrator requires of a use case at runtime, beside what its
code declares — for every tenant or for one — without a deploy.

    auth = AccessControl[BillingPermission](providers=jwt, runtime=True)
    auth.runtime.require(CommandApprovePayment, BillingPermission.APPROVE)         every tenant
    auth.runtime.require(CommandApprovePayment, auth.role("cash.lead@bo"), tenant="bo")
    auth.runtime.release(CommandApprovePayment, tenant="bo")

Context: the facade of `RequirementAdministration`, where what it only restricts, who may change
it and how each replica reads it are decided.
"""

import copy
import time
from collections.abc import Callable, Iterable
from datetime import timedelta

from sincpro_framework.auth.domain.added_requirements import (
    AddedRequirement,
    AddedRequirementStore,
)
from sincpro_framework.auth.domain.administration import AdministrationPolicy
from sincpro_framework.auth.domain.events import EventPublisher
from sincpro_framework.auth.domain.permissions import AccessCall, Permission, RoleRequirement
from sincpro_framework.auth.infrastructure.locks import AdministrationLocks
from sincpro_framework.auth.services.requirement_administration import (
    Judging,
    RequirementAdministration,
)


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
        given, read when asked: a permission no code declares protects nothing. `policy` bounds
        who may change them (`BoundedAdministration()` when not given)."""
        self._administration = RequirementAdministration(
            access, store, permissions, policy, locks, check_every, clock, publisher
        )

    @property
    def store(self) -> AddedRequirementStore:
        """Where the requirements are kept — shared by every replica reading it."""
        return self._administration.store

    @property
    def permissions(self) -> dict[str, Permission]:
        """What may be required, by value."""
        return self._administration.permissions

    def judged_by(self, access: Judging) -> "RuntimeRequirements":
        """These requirements — the same store, bound, leases and what was read — judged by
        `access`: what `AccessControl.using` gives the copy it makes."""
        judged = copy.copy(self)
        judged._administration = self._administration.judged_by(access)
        return judged

    def check(self, call: AccessCall) -> bool:
        """The `every_call` check: what is required of `call.command` for the caller's tenant,
        its refusal raised naming what was missing."""
        return self._administration.check(call)

    def required(self, tenant: str | None = None) -> tuple[AddedRequirement, ...]:
        """The requirements added for exactly this scope — what a screen lists."""
        return self._administration.required(tenant)

    def require(
        self,
        command: type | str,
        *required: str | RoleRequirement,
        tenant: str | None = None,
    ) -> AddedRequirement:
        """From now on `command`'s use case also requires every permission named and, with an
        `auth.role(...)`, one of its roles — for `tenant`, or every tenant — replacing what was
        required of it in that scope before; published (`RequirementAdded`).

            auth.runtime.require(CommandRefund, BillingPermission.APPROVE, tenant="bo")
            auth.runtime.require(CommandRefund, auth.role("cash.lead@bo"), tenant="bo")
        """
        return self._administration.require(command, *required, tenant=tenant)

    def release(self, command: type | str, tenant: str | None = None) -> None:
        """Nothing required of `command`'s use case at runtime in that scope any more;
        published (`RequirementRemoved`)."""
        self._administration.release(command, tenant)
