"""What a use case or a hook may require: a permission of the bounded context's own, one among
several, and what happens when it is missing.

    class BillingPermission(Permission):
        ISSUE_INVOICE = "billing.invoice.issue"

Context: the member is for code — "find usages" shows every handler that requires it, and
renaming it is a safe refactor; the value is for the wire — tokens, the IdP, `describe()` carry
it, so changing it is a breaking change. The base is empty on purpose: Python lets an enum with
no members be extended.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


class Permission(StrEnum):
    """Written `context.aggregate.action`: `billing.invoice.issue`."""


class WhenDenied(StrEnum):
    """What a guarded use case or hook does when the identity lacks what it requires."""

    RAISE = "raise"
    """`PermissionDenied`, or `Unauthenticated` when nobody is calling."""
    SKIP = "skip"
    """It does not run: a use case answers `None`, a hook's moment is left out — an optional
    step of an orchestration."""


@dataclass(frozen=True)
class AnyOf:
    """Met when one of its permissions is — `auth.any_of(...)`."""

    permissions: tuple[Permission, ...]

    def __str__(self) -> str:
        return " or ".join(str(one) for one in self.permissions)


type Requirement = Permission | AnyOf


@dataclass(frozen=True)
class Declaration:
    """What a use case or a hook declared: `public`, `authenticated`, or the requirements it
    needs, and what it does when they are missing."""

    kind: Literal["public", "authenticated", "requires"]
    requirements: tuple[Requirement, ...] = ()
    when_denied: WhenDenied = WhenDenied.RAISE

    def __str__(self) -> str:
        if self.kind != "requires":
            return self.kind
        needed = " and ".join(str(one) for one in self.requirements)
        return needed if self.when_denied == WhenDenied.RAISE else f"{needed} (else skipped)"
