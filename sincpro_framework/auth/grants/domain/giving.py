"""What an administrator may give — and therefore take away: the rule every assignment, level,
role definition and removal asks of who acts.

    givable(policy, ana, composition, "billing.manager", "assigning billing.manager")
    definable(policy, ana, composition, definition, "defining cash.desk@bo")

| Role | A bounded administrator may give it when |
|---|---|
| one it holds (its resolved roles, their closure) | it holds the permissions the role grants too |
| one its tenant defined itself (`x@<tenant>`) | it may give every role that role implies, and holds every permission it grants |
| any other | never — not even one that grants nothing: a requirement `auth.role(...)` may ask for its key |

Context: comparing permissions alone would let a role that grants nothing — but that
`auth.role("billing.admin")` requires — be given by anyone; a role its tenant defined is its
administrators' own composition, judged by what it is made of — as stored, so even one that no
longer builds can be fixed or removed by who could give it. Whoever may give a role may take it
away; a weaker administrator never strips a stronger one. The system and a superadmin pass
without the catalog being asked.
"""

from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.domain.administration import AdministrationPolicy
from sincpro_framework.auth.domain.identity import Identity
from sincpro_framework.auth.grants.domain.catalog import Composition
from sincpro_framework.auth.grants.domain.definitions import RoleDefinition


def _judged(
    policy: AdministrationPolicy,
    actor: Identity,
    composition: Composition,
    role: str,
    what: str,
    judged: set[str],
) -> None:
    """`givable` for `role`, each role judged once however many paths reach it — a lattice of
    a tenant's own roles costs what its roles count, never what its paths do."""
    if role in judged:
        return
    judged.add(role)
    catalog = composition.catalog
    if role not in catalog.roles:
        raise exceptions.InvalidGrant(f"{role} is not a role")
    if role in composition.own:
        declared = catalog.roles[role]
        for implied in declared.implies:
            _judged(policy, actor, composition, implied.key, what, judged)
        policy.holding(actor, what, permissions=declared.grants)
    elif role in actor.roles:
        policy.holding(actor, what, permissions=catalog.permissions_of({role}))
    else:
        policy.holding(actor, what, roles={role})


def givable(
    policy: AdministrationPolicy,
    actor: Identity,
    composition: Composition,
    role: str,
    what: str,
) -> None:
    """Raise unless `actor` may give `role` in the tenant `composition` is of.

    1. Unbounded by `policy`: it may.
    2. A role the catalog does not declare: `InvalidGrant`.
    3. A role its tenant defined: when it may give each role implied and holds what it grants.
    4. A role it holds: when it holds what the role grants in the catalog.
    Final: otherwise refused, naming the role.
    """
    if not policy.unbounded(actor):
        _judged(policy, actor, composition, role, what, set())


def definable(
    policy: AdministrationPolicy,
    actor: Identity,
    composition: Composition,
    definition: RoleDefinition,
    what: str,
) -> None:
    """Raise unless `actor` may give what `definition` is made of — every role it implies, every
    permission it grants."""
    if policy.unbounded(actor):
        return
    judged: set[str] = set()
    for implied in sorted(definition.implies):
        _judged(policy, actor, composition, implied, what, judged)
    policy.holding(actor, what, permissions=definition.grants)
