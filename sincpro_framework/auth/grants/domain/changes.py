"""A change to who holds what, as a project's own rules are asked about it — `@grants.allow`,
beside the bound every administration keeps.

    @grants.allow
    def owners_give_admin(change: GrantChange) -> bool:
        return change.role != "billing.admin" or "billing.owner" in change.actor.roles

Context: a rule is asked before the bound (`AdministrationPolicy`), which is asked under the
tenant's lease; both must let the change through, so a rule only restricts: answering `False` refuses it (`PermissionDenied` naming the rule); raising an
`AuthError` of its own answers otherwise. The system is never asked about.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal

from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.domain.identity import Identity, IdentityKind
from sincpro_framework.auth.grants.domain.definitions import RoleDefinition

Action = Literal["assign", "unassign", "set_level", "entitle", "withdraw", "define", "remove"]


@dataclass(frozen=True)
class GrantChange:
    """What an administrator is about to change, and who."""

    actor: Identity
    action: Action
    tenant: str | None = None
    subject: str | None = None
    """Whose roles change — assign, unassign, set_level."""
    role: str | None = None
    """The role given or taken — its catalog key; the level for set_level."""
    privilege: str | None = None
    definition: RoleDefinition | None = None
    """The role defined — define."""
    entitlement: str | None = None


@dataclass(frozen=True)
class Allow:
    """A project's own rule about changes, named for a refusal."""

    name: str
    check: Callable[[GrantChange], bool]


def allowed(rules: Iterable[Allow], change: GrantChange) -> None:
    """Raise unless every rule lets `change` through — the system passes without asking."""
    if change.actor.kind == IdentityKind.SYSTEM:
        return
    for rule in rules:
        if not rule.check(change):
            raise exceptions.PermissionDenied(
                change.actor.subject, rule.name, f"{rule.name} does not hold"
            )
