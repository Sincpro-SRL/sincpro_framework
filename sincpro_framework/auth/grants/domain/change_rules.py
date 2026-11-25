"""A change to who holds what, and the project's own rules checked against it — `@grants.allow`,
beside the bound every administration enforces.

    @grants.allow
    def owners_give_admin(change: GrantChange) -> bool:
        return change.role != "billing.admin" or "billing.owner" in change.actor.roles

Context: the rules are checked before the bound (`AdministrationPolicy`), which is checked under
the tenant's lease; both must let the change through, so a rule only restricts: answering
`False` refuses it (`PermissionDenied` naming the rule); raising an `AuthError` of its own
answers otherwise. A change the system makes is never checked.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.domain.identity import Identity, IdentityKind
from sincpro_framework.auth.grants.domain.definitions import RoleDefinition


class GrantAction(StrEnum):
    """What an administrator is about to do — what a rule of `@grants.allow` reads."""

    ASSIGN = "assign"
    UNASSIGN = "unassign"
    SET_LEVEL = "set_level"
    ENTITLE = "entitle"
    WITHDRAW = "withdraw"
    DEFINE = "define"
    REMOVE = "remove"


@dataclass(frozen=True)
class GrantChange:
    """What an administrator is about to change, and who."""

    actor: Identity
    action: GrantAction
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
class ChangeRule:
    """A project's own rule about changes, named for a refusal."""

    name: str
    allows: Callable[[GrantChange], bool]


class ChangeRules:
    """The rules a project registered, checked against every change an administrator makes."""

    def __init__(self) -> None:
        self.rules: list[ChangeRule] = []

    def add(self, rule: ChangeRule) -> None:
        """`rule` checked against every change from now on."""
        self.rules.append(rule)

    def check(self, change: GrantChange) -> None:
        """`PermissionDenied`, naming the rule, unless every rule allows `change` — a change
        the system makes passes unchecked."""
        if change.actor.kind == IdentityKind.SYSTEM:
            return
        for rule in self.rules:
            if not rule.allows(change):
                raise exceptions.PermissionDenied(
                    change.actor.subject, rule.name, f"{rule.name} does not hold"
                )
