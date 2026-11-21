"""Roles defined at runtime — what an administrator adds from a screen, beside the roles the code
declares: what each grants (permissions the code already checks), what it implies, its
privilege and level.

    RoleDefinition(key="billing.auditor", name="Auditor", grants={"billing.invoice.read"},
                   implies={"billing.clerk"})

Context: a definition is data, kept by a `RoleDefinitions` store as one versioned document, so
two administrators writing at once never lose each other's change — the second writes against
the version it read, and is told to read again. A role the code declares is never redefined
here: it changes in code, reviewed with the use cases that require what it grants.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence

from pydantic import ConfigDict

from sincpro_framework.sincpro_abstractions import DataTransferObject


class RoleDefinition(DataTransferObject):
    """A role as data: the keys of what it implies and of its privilege, not the objects."""

    model_config = ConfigDict(frozen=True)

    key: str
    name: str = ""
    grants: frozenset[str] = frozenset()
    """Permissions — each one the code declares."""
    implies: frozenset[str] = frozenset()
    """Keys of roles, of the code or defined here."""
    privilege: str | None = None
    """The key of a privilege the code declares, when this role is one of its levels."""
    sequence: int = 0
    disjoint: frozenset[str] = frozenset()
    description: str = ""


class DefinedRoles(DataTransferObject):
    """Every role defined at runtime, and the version they were written under."""

    model_config = ConfigDict(frozen=True)

    version: int = 0
    roles: tuple[RoleDefinition, ...] = ()


class RoleDefinitions(ABC):
    """The port: the roles defined at runtime, as one document written only over the version
    it was read at."""

    @abstractmethod
    def read(self) -> DefinedRoles:
        """Every role defined, and their version — `DefinedRoles()` before the first write."""

    @abstractmethod
    def write(self, expected_version: int, roles: Sequence[RoleDefinition]) -> bool:
        """Keep `roles` as version `expected_version + 1` — only when the version kept now is
        `expected_version`, atomically. `False` when another write came first."""
