"""Roles defined at runtime — what an administrator adds from a screen, beside the roles the code
declares: what each grants (permissions the code already checks), what it implies, its
privilege and level — for every tenant (the platform's) or for one tenant (that tenant's).

    RoleDefinition(key="billing.auditor", grants=frozenset({"billing.invoice.read"}),
                   implies=frozenset({"billing.clerk"}))           every tenant: "billing.auditor"
    RoleDefinition(key="cash.desk", tenant="bo",
                   implies=frozenset({"billing.clerk"}))            tenant bo: "cash.desk@bo"

Context: a tenant's role lives in its tenant's own namespace — `<key>@<tenant>`, its `catalog_key` —
so it can never be taken for a role of the code or of every tenant: no tenant meets an
`auth.role("finance.approver")`, nor a platform's requirement, with a role it named so itself.
What it implies is named by catalog key: a role of the code, of every tenant, or of its own
tenant (`cash.clerk@bo`). Each scope is one versioned document; a key removed is listed `retired`
in it, so assignments left behind never come back to life under a role defined later with that
name. A role the code declares is never redefined here: it changes in code.
"""

from typing import Any

from pydantic import ConfigDict

from sincpro_framework.auth.common.domain.documents import ScopedDocuments
from sincpro_framework.sincpro_abstractions import DataTransferObject

TENANT_SEPARATOR = "@"
"""What separates a tenant role's own key from its tenant: `cash.desk@bo`."""


def catalog_key(key: str, tenant: str | None) -> str:
    """The key a role is held and required by: its own for every tenant's, `<key>@<tenant>` for
    a tenant's."""
    return key if tenant is None else f"{key}{TENANT_SEPARATOR}{tenant}"


class RoleDefinition(DataTransferObject):
    """A role as data: the catalog keys of what it implies, the key of its privilege."""

    model_config = ConfigDict(frozen=True)

    key: str
    """Its own name — never with `@`: a tenant's role is named apart by its `catalog_key`."""
    tenant: str | None = None
    """The tenant it exists for — `None`: every tenant, the platform's."""
    name: str = ""
    grants: frozenset[str] = frozenset()
    """Permissions — each one the code declares."""
    implies: frozenset[str] = frozenset()
    """Catalog keys: roles of the code, of every tenant, or of its own tenant (`x@<tenant>`)."""
    privilege: str | None = None
    """The key of a privilege the code declares, when this role is one of its levels."""
    sequence: int = 0
    disjoint: frozenset[str] = frozenset()
    """Catalog keys of roles never held together with it."""
    description: str = ""

    @property
    def catalog_key(self) -> str:
        """The key it is held and required by — `<key>@<tenant>` for a tenant's."""
        return catalog_key(self.key, self.tenant)

    def same_shape(self, other: "RoleDefinition") -> bool:
        """Whether `other` relates to other roles as this one does — implies, privilege, level,
        disjoint: what a role already assigned may not change under its holders."""
        return (self.implies, self.privilege, self.sequence, self.disjoint) == (
            other.implies,
            other.privilege,
            other.sequence,
            other.disjoint,
        )

    def is_narrower_than(self, before: "RoleDefinition") -> bool:
        """Whether this definition may hold less than `before` — fewer grants, or fewer roles
        implied: what a holder could lose when it replaces `before`."""
        return not (before.grants <= self.grants and before.implies <= self.implies)

    def event_fields(self) -> dict[str, Any]:
        """What an event says this role is."""
        return {
            "role_name": self.name,
            "grants": sorted(self.grants),
            "implies": sorted(self.implies),
            "privilege": self.privilege,
            "sequence": self.sequence,
            "disjoint": sorted(self.disjoint),
        }


class DefinedRoles(DataTransferObject):
    """The roles defined for one scope, the keys it retired, and the version written under."""

    model_config = ConfigDict(frozen=True)

    version: int = 0
    roles: tuple[RoleDefinition, ...] = ()
    retired: frozenset[str] = frozenset()
    """Keys removed from this scope — never defined in it again."""

    def role(self, key: str) -> RoleDefinition | None:
        """The role of own key `key` defined here — `None` when it is not."""
        return next((one for one in self.roles if one.key == key), None)

    def with_role(self, definition: RoleDefinition) -> "DefinedRoles":
        """The next version, with `definition` new or in place of the one of its key."""
        others = tuple(one for one in self.roles if one.key != definition.key)
        return DefinedRoles(
            version=self.version + 1, roles=(*others, definition), retired=self.retired
        )

    def without_role(self, key: str) -> "DefinedRoles":
        """The next version, without `key` — retired."""
        return DefinedRoles(
            version=self.version + 1,
            roles=tuple(one for one in self.roles if one.key != key),
            retired=self.retired | {key},
        )


class RoleDefinitions(ScopedDocuments[DefinedRoles]):
    """The port: the roles defined at runtime, one `DefinedRoles` per scope — `None` for every
    tenant, a tenant for its own."""
