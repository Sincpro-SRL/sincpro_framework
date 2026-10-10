"""`RolePermissions`: which permissions each role grants — what a provider uses when the
credential carries roles and the use cases require permissions.

    grants = RolePermissions(
        {"accountant": {BillingPermission.ISSUE_INVOICE}, "billing.admin": set(BillingPermission)},
        implies={"billing.admin": {"accountant"}},
    )
    grants.resolve(Identity.user("user:1", roles={"accountant"}))   →  permissions filled

Context: a role implies others, as Odoo's implied groups — a role grants its own permissions and
those of every role it implies, transitively. A role nobody declared grants nothing.
"""

from collections.abc import Iterable, Mapping

from sincpro_framework.auth.domain.identity import Identity


class RolePermissions:
    def __init__(
        self,
        roles: Mapping[str, Iterable[str]],
        implies: Mapping[str, Iterable[str]] | None = None,
    ) -> None:
        self.grants = {str(role): frozenset(granted) for role, granted in roles.items()}
        self.implies = {str(role): frozenset(more) for role, more in (implies or {}).items()}

    def roles_held(self, roles: Iterable[str]) -> frozenset[str]:
        held: set[str] = set()
        pending = [str(role) for role in roles]
        while pending:
            role = pending.pop()
            if role not in held:
                held.add(role)
                pending.extend(self.implies.get(role, ()))
        return frozenset(held)

    def permissions_of(self, roles: Iterable[str]) -> frozenset[str]:
        return frozenset().union(
            *(self.grants.get(role, frozenset()) for role in self.roles_held(roles))
        )

    def resolve(self, identity: Identity) -> Identity:
        """`identity` with the permissions its roles grant added to the ones it had."""
        granted = self.permissions_of(identity.roles)
        return identity.model_copy(update={"permissions": identity.permissions | granted})
