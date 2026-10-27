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
        """Its permissions joined by `or` — `billing.read or billing.admin`."""
        return " or ".join(str(one) for one in self.permissions)


@dataclass(frozen=True)
class ByField:
    """Met as the permission the Command's own `field` names — `auth.by_field(...)`: what a
    use case needs depends on what it was asked.

        ByField("direction", (("inbound", RECEIVE), ("outbound", PAY)))
            CommandRegisterPayment(direction="outbound")   ->  needs PAY
            no Command — a catalog asking                  ->  RECEIVE or PAY
    """

    field: str
    permissions: tuple[tuple[object, Permission | AnyOf], ...]

    def chosen(self, dto: object) -> "Permission | AnyOf | None":
        """The requirement `dto`'s field names — `None` when its value names none."""
        value = getattr(dto, self.field, None)
        return next((needed for one, needed in self.permissions if one == value), None)

    def every(self) -> AnyOf:
        """One among every permission it may name — what is asked with no Command at hand."""
        found: list[Permission] = []
        for _, needed in self.permissions:
            for one in needed.permissions if isinstance(needed, AnyOf) else (needed,):
                if one not in found:
                    found.append(one)
        return AnyOf(tuple(found))

    def __str__(self) -> str:
        """`direction: inbound → finance.receive, outbound → finance.pay`."""
        named = ", ".join(f"{value} → {needed}" for value, needed in self.permissions)
        return f"{self.field}: {named}"


type Requirement = Permission | AnyOf | ByField


@dataclass(frozen=True)
class AccessDeclaration:
    """What a use case or a hook declared: `public`, `authenticated`, or the requirements it
    needs, and what it does when they are missing."""

    kind: Literal["public", "authenticated", "requires"]
    requirements: tuple[Requirement, ...] = ()
    when_denied: WhenDenied = WhenDenied.RAISE

    def __str__(self) -> str:
        """How `describe()` and the logs say it.

        AccessDeclaration("public")                                   ->  "public"
        AccessDeclaration("requires", (A, AnyOf((B, C))))             ->  "a and b or c"
        AccessDeclaration("requires", (A,), WhenDenied.SKIP)          ->  "a (else skipped)"
        """
        if self.kind != "requires":
            return self.kind
        needed = " and ".join(str(one) for one in self.requirements)
        return needed if self.when_denied == WhenDenied.RAISE else f"{needed} (else skipped)"
