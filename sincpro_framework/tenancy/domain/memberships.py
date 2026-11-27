"""Memberships — which tenants a subject belongs to, and with which role: the *allowed* tenants
of an execution (N users ↔ N tenants).

    memberships.tenants_of("user:ana")    ->  {"acme": Membership("acme", "admin"),
                                               "globex": Membership("globex", "member")}

A port, like the catalog: an identity provider's organizations, a table, an Odoo's
`company_ids` — each answers it with its own adapter.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Membership:
    """A subject's place in one tenant."""

    tenant: str
    role: str | None = None


class Memberships(ABC):
    """The tenants each subject belongs to. `tenants_of` is the whole contract to read them;
    `members_of`, `join` and `leave` are what memberships the service administers add."""

    @abstractmethod
    def tenants_of(self, subject: str) -> Mapping[str, Membership]:
        """The subject's memberships by tenant id — empty for a subject of no tenant."""

    def members_of(self, tenant: str) -> dict[str, str | None]:
        """Every subject of `tenant`, with its role."""
        raise NotImplementedError(f"{type(self).__name__} does not list a tenant's members")

    def join(self, subject: str, tenant: str, role: str | None = None) -> None:
        """Makes `subject` a member of `tenant`, with `role` — replacing the role it had there."""
        raise NotImplementedError(
            f"{type(self).__name__} is read here, administered elsewhere"
        )

    def leave(self, subject: str, tenant: str) -> None:
        """Takes `subject` out of `tenant`."""
        raise NotImplementedError(
            f"{type(self).__name__} is read here, administered elsewhere"
        )
