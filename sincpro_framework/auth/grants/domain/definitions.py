"""Roles defined at runtime — what an administrator adds from a screen, beside the roles the code
declares: what each grants (permissions the code already checks), what it implies, its
privilege and level — for every tenant (the platform's) or for one tenant (that tenant's).

    RoleDefinition(key="billing.auditor", name="Auditor", grants=frozenset({"billing.invoice.read"}),
                   implies=frozenset({"billing.clerk"}))                      every tenant
    RoleDefinition(key="cash.supervisor", tenant="bo", ...)                   tenant bo only

Context: each scope — what every tenant shares, and each tenant — is one versioned document,
written only over the version it was read at, so two administrators never lose each other's
change and one tenant's change never makes a replica read another's. A role the code declares
is never redefined here: it changes in code, reviewed with the use cases that require what it
grants.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence

from pydantic import ConfigDict

from sincpro_framework.sincpro_abstractions import DataTransferObject


class RoleDefinition(DataTransferObject):
    """A role as data: the keys of what it implies and of its privilege, not the objects."""

    model_config = ConfigDict(frozen=True)

    key: str
    tenant: str | None = None
    """The tenant it exists for — `None`: every tenant, the platform's."""
    name: str = ""
    grants: frozenset[str] = frozenset()
    """Permissions — each one the code declares."""
    implies: frozenset[str] = frozenset()
    """Keys of roles of the code, of every tenant, or of its own tenant."""
    privilege: str | None = None
    """The key of a privilege the code declares, when this role is one of its levels."""
    sequence: int = 0
    disjoint: frozenset[str] = frozenset()
    description: str = ""


class DefinedRoles(DataTransferObject):
    """The roles defined for one scope, and the version they were written under."""

    model_config = ConfigDict(frozen=True)

    version: int = 0
    roles: tuple[RoleDefinition, ...] = ()


class RoleDefinitions(ABC):
    """The port: the roles defined at runtime, one document per scope — `None` for every
    tenant, a tenant for its own — written only over the version it was read at."""

    @abstractmethod
    def read(self, tenant: str | None = None) -> DefinedRoles:
        """The roles of the scope, and their version — `DefinedRoles()` before the first write."""

    @abstractmethod
    def write(
        self, tenant: str | None, expected_version: int, roles: Sequence[RoleDefinition]
    ) -> bool:
        """Keep `roles` as the scope's version `expected_version + 1` — only when the version
        kept now is `expected_version`, atomically. `False` when another write came first."""
