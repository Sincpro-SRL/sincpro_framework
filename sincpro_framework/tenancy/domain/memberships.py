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
    """The tenants each subject belongs to. `tenants_of` is the whole contract."""

    @abstractmethod
    def tenants_of(self, subject: str) -> Mapping[str, Membership]:
        """The subject's memberships by tenant id — empty for a subject of no tenant."""
