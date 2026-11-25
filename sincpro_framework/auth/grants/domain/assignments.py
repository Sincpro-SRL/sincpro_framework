"""Who holds which role: an assignment given to a subject in a tenant, what a subject holds, and
the store that holds them — only what was assigned; the closure is the catalog's.

    store.assign(Assignment(subject="user:ana", tenant="bo", role="billing.manager"))
    store.grants_of("user:ana", "bo")   ->  SubjectGrants(roles={"billing.manager"}, entitlements=…)
"""

from abc import ABC, abstractmethod
from datetime import UTC, datetime

from pydantic import ConfigDict

from sincpro_framework.sincpro_abstractions import DataTransferObject


class Assignment(DataTransferObject):
    """A role given to a subject in a tenant — until `valid_until`, when it is temporary."""

    model_config = ConfigDict(frozen=True)

    subject: str
    tenant: str | None = None
    role: str
    valid_until: datetime | None = None

    def is_active_at(self, now: datetime) -> bool:
        """Whether the assignment is in force at `now`."""
        return self.valid_until is None or now < self.valid_until


class SubjectGrants(DataTransferObject):
    """What a subject holds in a tenant, as a store records it: the roles assigned, and what the
    tenant is entitled to — `None` when the store records no entitlements."""

    model_config = ConfigDict(frozen=True)

    roles: frozenset[str] = frozenset()
    entitlements: frozenset[str] | None = None


class GrantSource(ABC):
    """The port: what a subject holds in a tenant."""

    @abstractmethod
    def grants_of(self, subject: str, tenant: str | None) -> SubjectGrants:
        """The roles assigned to `subject` in `tenant` and in force, and the tenant's
        entitlements when the source records them."""


class GrantStore(GrantSource):
    """A `GrantSource` the service writes — assignments and entitlements, stored as given."""

    @abstractmethod
    def assign(self, assignment: Assignment) -> None:
        """Give the role — replacing the same role's earlier assignment to the subject."""

    @abstractmethod
    def unassign(self, subject: str, tenant: str | None, role: str) -> None:
        """Take the role back; nothing when it was not given."""

    @abstractmethod
    def assignments_of(self, subject: str, tenant: str | None) -> list[Assignment]:
        """Every assignment of `subject` in `tenant`, in force or not."""

    @abstractmethod
    def holders(self, role: str, tenant: str | None) -> list[Assignment]:
        """Who was given `role` in `tenant` — assigned directly, not through an implication."""

    @abstractmethod
    def entitle(self, tenant: str, entitlement: str) -> None:
        """The tenant has `entitlement` from now on."""

    @abstractmethod
    def withdraw(self, tenant: str, entitlement: str) -> None:
        """The tenant no longer has `entitlement`."""

    @abstractmethod
    def entitlements_of(self, tenant: str | None) -> frozenset[str]:
        """What the tenant is entitled to."""

    def now(self) -> datetime:
        """The moment assignments are judged in force at — UTC now unless overridden."""
        return datetime.now(UTC)

    def grants_of(self, subject: str, tenant: str | None) -> SubjectGrants:
        """The roles assigned and in force now, and the tenant's entitlements."""
        moment = self.now()
        return SubjectGrants(
            roles=frozenset(
                one.role
                for one in self.assignments_of(subject, tenant)
                if one.is_active_at(moment)
            ),
            entitlements=self.entitlements_of(tenant),
        )
