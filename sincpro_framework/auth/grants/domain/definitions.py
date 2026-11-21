"""Roles defined at runtime — what an administrator adds from a screen, beside the roles the code
declares: what each grants (permissions the code already checks), what it implies, its
privilege and level — for every tenant (the platform's) or for one tenant (that tenant's).

    RoleDefinition(key="billing.auditor", name="Auditor", grants=frozenset({"billing.invoice.read"}),
                   implies=frozenset({"billing.clerk"}))                      every tenant
    RoleDefinition(key="cash.supervisor", tenant="bo", ...)                   tenant bo only

Context: each scope — what every tenant shares, and each tenant — is one versioned document,
written only over the version it was read at, so two administrators never lose each other's
change and one tenant's change never makes a replica read another's. A key removed stays
`retired` in its scope: assignments left behind never come back to life under a role defined
later with the same name. A role the code declares is never redefined here: it changes in code.
"""

from abc import ABC, abstractmethod

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

    def same_shape(self, other: "RoleDefinition") -> bool:
        """Whether `other` relates to other roles as this one does — implies, privilege, level,
        disjoint: what a role already assigned may not change under its holders."""
        return (self.implies, self.privilege, self.sequence, self.disjoint) == (
            other.implies,
            other.privilege,
            other.sequence,
            other.disjoint,
        )


class DefinedRoles(DataTransferObject):
    """The roles defined for one scope, the keys it retired, and the version written under."""

    model_config = ConfigDict(frozen=True)

    version: int = 0
    roles: tuple[RoleDefinition, ...] = ()
    retired: frozenset[str] = frozenset()
    """Keys removed from this scope — never defined in it again."""

    def role(self, key: str) -> RoleDefinition | None:
        """The role `key` defined here — `None` when it is not."""
        return next((one for one in self.roles if one.key == key), None)

    def replacing(self, definition: RoleDefinition) -> "DefinedRoles":
        """The next version, with `definition` new or in place of the one of its key."""
        kept = tuple(one for one in self.roles if one.key != definition.key)
        return DefinedRoles(
            version=self.version + 1, roles=(*kept, definition), retired=self.retired
        )

    def without(self, key: str) -> "DefinedRoles":
        """The next version, without `key` — retired."""
        return DefinedRoles(
            version=self.version + 1,
            roles=tuple(one for one in self.roles if one.key != key),
            retired=self.retired | {key},
        )


class RoleDefinitions(ABC):
    """The port: the roles defined at runtime, one document per scope — `None` for every
    tenant, a tenant for its own — written only over the version it was read at."""

    @abstractmethod
    def read(self, tenant: str | None = None) -> DefinedRoles:
        """The scope's document — `DefinedRoles()` before the first write."""

    @abstractmethod
    def write(self, tenant: str | None, document: DefinedRoles) -> bool:
        """Keep `document` as the scope's — only when the version kept now is the one before
        `document.version`, atomically. `False` when another write came first."""
