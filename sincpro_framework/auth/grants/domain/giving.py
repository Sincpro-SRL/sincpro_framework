"""What an administrator may give — and therefore take away: the rule every assignment, level,
role definition and removal asks of who acts.

    givable(policy, ana, composition, "billing.manager", "assigning billing.manager")

| Role | Given by a bounded administrator when |
|---|---|
| one it holds (its resolved roles, their closure) | it holds the permissions the role grants too |
| one its tenant defined itself | it may give every role that role implies, and holds every permission it grants |
| any other | never — not even one that grants nothing: a requirement `auth.role(...)` may ask for its key |

Context: comparing permissions alone would let a role that grants nothing — but that
`auth.role("billing.admin")` requires — be given by anyone; and a role defined by the tenant is
its administrators' own composition, so it is judged by what it is made of. Whoever may give a
role may take it away; a weaker administrator never strips a stronger one.
"""

from sincpro_framework.auth.domain.administration import AdministrationPolicy
from sincpro_framework.auth.domain.identity import Identity
from sincpro_framework.auth.grants.domain.catalog import Composition


def givable(
    policy: AdministrationPolicy,
    actor: Identity,
    composition: Composition,
    role: str,
    what: str,
) -> None:
    """Raise unless `actor` may give `role` in the tenant `composition` is of.

    1. A role the catalog does not declare: `InvalidGrant` — whoever acts.
    2. Unbounded by `policy`: it may.
    3. A role it holds: when it holds what the role grants.
    4. A role its tenant defined: when it may give each role implied and holds what it grants.
    Final: otherwise refused, naming the role.
    """
    catalog = composition.catalog
    declared = catalog.role(role)
    if policy.unbounded(actor):
        return
    if role in actor.roles:
        policy.holding(actor, what, permissions=catalog.permissions_of({role}))
        return
    if role in composition.own:
        for implied in declared.implies:
            givable(policy, actor, composition, implied.key, what)
        policy.holding(actor, what, permissions=declared.grants)
        return
    policy.holding(actor, what, roles={role})
