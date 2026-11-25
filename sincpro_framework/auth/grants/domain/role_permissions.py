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

from sincpro_framework.auth.common.domain.identity import Identity
from sincpro_framework.auth.grants.domain.catalog import implied_closure


class RolePermissions:
    """A table of roles to permissions, with implied roles — resolved transitively.

    Context: a credential (a JWT's `roles`, Odoo's groups) speaks of roles, the guard of
    permissions; this is the translation a provider applies once, at authentication.
    """

    def __init__(
        self,
        roles: Mapping[str, Iterable[str]],
        implies: Mapping[str, Iterable[str]] | None = None,
    ) -> None:
        """The table: what each role grants, and which roles each role implies — role names
        and permissions taken as given, as frozen sets."""
        self.grants = {str(role): frozenset(granted) for role, granted in roles.items()}
        self.implies = {str(role): frozenset(more) for role, more in (implies or {}).items()}

    def roles_held(self, roles: Iterable[str]) -> frozenset[str]:
        """`roles` and every role they imply, transitively.

            RolePermissions({}, implies={"admin": {"accountant"}, "accountant": {"viewer"}})
                .roles_held({"admin"})   ->  {"admin", "accountant", "viewer"}

        Context: the catalog's own walk (`implied_closure`) — a cycle of implied roles ends.
        """
        return implied_closure(
            (str(role) for role in roles), lambda role: self.implies.get(role, frozenset())
        )

    def permissions_of(self, roles: Iterable[str]) -> frozenset[str]:
        """The union of what every role held grants — a role nobody declared adds nothing."""
        return frozenset().union(
            *(self.grants.get(role, frozenset()) for role in self.roles_held(roles))
        )

    def resolve(self, identity: Identity) -> Identity:
        """`identity` with the permissions its roles grant added to the ones it had."""
        granted = self.permissions_of(identity.roles)
        return identity.model_copy(update={"permissions": identity.permissions | granted})
