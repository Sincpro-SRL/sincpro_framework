"""`AssignmentAuthority`: what an administrator may assign — and therefore take away: the rule
every assignment, level, role definition and removal checks against who acts.

    authority = AssignmentAuthority(policy)
    authority.check_role(ana, effective, "billing.manager", "assigning billing.manager")
    authority.check_definition(ana, effective, definition, "defining cash.desk@bo")

| Role | A bounded administrator may assign it when |
|---|---|
| one it holds (its resolved roles, their closure) | it holds the permissions the role grants too |
| one its tenant defined itself (`x@<tenant>`) | it may assign every role that role implies, and holds every permission it grants |
| any other | never — not even one that grants nothing: a requirement `auth.role(...)` may ask for its key |

Context: comparing permissions alone would let a role that grants nothing — but that
`auth.role("billing.admin")` requires — be assigned by anyone; a role its tenant defined is its
administrators' own composition, judged by what it is made of — as stored, so even one that no
longer builds can be fixed or removed by who could assign it. Whoever may assign a role may take
it away; a weaker administrator never strips a stronger one. The system and a superadmin pass
without the catalog being consulted.
"""

from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.domain.administration import AdministrationPolicy
from sincpro_framework.auth.common.domain.identity import Identity
from sincpro_framework.auth.grants.domain.catalog import EffectiveCatalog
from sincpro_framework.auth.grants.domain.definitions import RoleDefinition


class AssignmentAuthority:
    """Which roles an actor may assign, bounded by `policy`."""

    def __init__(self, policy: AdministrationPolicy) -> None:
        self.policy = policy

    def _check_once(
        self,
        actor: Identity,
        effective: EffectiveCatalog,
        role: str,
        what: str,
        checked: set[str],
    ) -> None:
        """`check_role` for `role`, each role checked once however many paths reach it — a
        lattice of a tenant's own roles costs what its roles count, never what its paths do.
        """
        if role in checked:
            return
        checked.add(role)
        catalog = effective.catalog
        if role not in catalog.roles:
            raise exceptions.InvalidGrant(f"{role} is not a role")
        if role in effective.own:
            declared = catalog.roles[role]
            for implied in declared.implies:
                self._check_once(actor, effective, implied.key, what, checked)
            self.policy.validate_holds(actor, what, permissions=declared.grants)
        elif role in actor.roles:
            self.policy.validate_holds(
                actor, what, permissions=catalog.permissions_of({role})
            )
        else:
            self.policy.validate_holds(actor, what, roles={role})

    def check_role(
        self, actor: Identity, effective: EffectiveCatalog, role: str, what: str
    ) -> None:
        """Raise unless `actor` may assign `role` in the tenant `effective` is of.

        1. Unbounded by `policy`: it may.
        2. A role the catalog does not declare: `InvalidGrant`.
        3. A role its tenant defined: when it may assign each role implied and holds what it
           grants.
        4. A role it holds: when it holds what the role grants in the catalog.
        Final: otherwise refused, naming the role.
        """
        if not self.policy.is_unrestricted(actor):
            self._check_once(actor, effective, role, what, set())

    def check_definition(
        self,
        actor: Identity,
        effective: EffectiveCatalog,
        definition: RoleDefinition,
        what: str,
    ) -> None:
        """Raise unless `actor` may assign what `definition` is made of — every role it
        implies, every permission it grants."""
        if self.policy.is_unrestricted(actor):
            return
        checked: set[str] = set()
        for implied in sorted(definition.implies):
            self._check_once(actor, effective, implied, what, checked)
        self.policy.validate_holds(actor, what, permissions=definition.grants)
