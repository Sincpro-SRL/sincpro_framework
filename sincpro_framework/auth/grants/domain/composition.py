"""The catalog in effect for a scope, composed from the code's and what is defined at runtime —
pure, no store: what `LiveCatalog` reads with and `RoleAdministration` checks a change against.

    built(code, [auditor])                     ->  a catalog, or `InvalidGrant` saying why not
    compose(code, shared, own)                 ->  Composition(catalog, problems, own_keys)

Context: in a tenant's catalog its own roles win — a role every tenant shares, defined later
with the same key, is shadowed there, never silently granting the tenant's holders what nobody
assigned them. A defined role that does not build grants nothing, and the others still do; a
retired key never builds again.
"""

from collections.abc import Iterable, Sequence

from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.grants.domain.catalog import (
    Composition,
    GrantCatalog,
    Privilege,
    Role,
)
from sincpro_framework.auth.grants.domain.definitions import DefinedRoles, RoleDefinition


def _privileges_of(code: GrantCatalog) -> dict[str, Privilege]:
    """The privileges the code's roles name, by key."""
    return {
        role.privilege.key: role.privilege
        for role in code.roles.values()
        if role.privilege is not None
    }


def built(code: GrantCatalog, defined: Sequence[RoleDefinition]) -> GrantCatalog:
    """The code's catalog with `defined` beside it — `InvalidGrant` saying why it cannot hold.

    1. A defined role named like one of the code, or two defined of one key: refused.
    2. Each defined role built once, what it implies first; a cycle, an unknown role or an
       unknown privilege: refused.
    3. Final: one catalog checked as the code's is — disjoint roles, exclusive levels — with the
       code's entitlements.
    """
    by_key: dict[str, RoleDefinition] = {}
    for one in defined:
        if one.key in code.roles:
            raise exceptions.InvalidGrant(f"{one.key}: declared in code, changed there")
        if one.key in by_key:
            raise exceptions.InvalidGrant(f"{one.key} is defined twice")
        by_key[one.key] = one
    privileges = _privileges_of(code)
    made: dict[str, Role] = {}

    def make(key: str, path: frozenset[str]) -> Role:
        if key in code.roles:
            return code.roles[key]
        if key in made:
            return made[key]
        if key in path:
            raise exceptions.InvalidGrant(f"{key} implies itself through {sorted(path)}")
        if key not in by_key:
            raise exceptions.InvalidGrant(f"{key} is not a role")
        definition = by_key[key]
        if definition.privilege is not None and definition.privilege not in privileges:
            raise exceptions.InvalidGrant(f"{definition.privilege} is not a privilege")
        made[key] = Role(
            key=key,
            name=definition.name or key,
            grants=definition.grants,
            implies=tuple(make(one, path | {key}) for one in sorted(definition.implies)),
            privilege=(
                None if definition.privilege is None else privileges[definition.privilege]
            ),
            sequence=definition.sequence,
            disjoint=definition.disjoint,
            description=definition.description,
        )
        return made[key]

    roles = [make(key, frozenset()) for key in sorted(by_key)]
    try:
        return GrantCatalog(
            [*code.roles.values(), *roles], entitlements=code.entitlements.values()
        )
    except exceptions.ProgrammingError as refused:
        raise exceptions.InvalidGrant(str(refused)) from refused


def tolerant(
    code: GrantCatalog, defined: Iterable[RoleDefinition]
) -> tuple[list[RoleDefinition], dict[str, str]]:
    """The defined roles that build with the code's, in the order given — and the others, with
    why. A role waits for what it implies, so the order defined does not matter."""
    accepted: list[RoleDefinition] = []
    pending = list(defined)
    why: dict[str, str] = {}
    moved = True
    while pending and moved:
        moved = False
        for one in list(pending):
            try:
                built(code, [*accepted, one])
            except exceptions.InvalidGrant as refused:
                why[one.key] = str(refused)
                continue
            accepted.append(one)
            pending.remove(one)
            moved = True
    return accepted, {one.key: why[one.key] for one in pending}


def compose(code: GrantCatalog, shared: DefinedRoles, own: DefinedRoles) -> Composition:
    """The catalog of a scope: the code's, what every tenant shares, and the scope's own.

    1. A shared role whose key the scope defines too: shadowed — the scope's own is in effect.
    2. Every remaining definition built tolerantly — the ones that do not build left out.
    Final: the catalog, what was left out and why, and the scope's own keys.
    """
    mine = {one.key for one in own.roles}
    shadowed = {
        one.key: f"{one.key} is also a role every tenant shares: this tenant's own is in effect"
        for one in shared.roles
        if one.key in mine
    }
    candidates = [*(one for one in shared.roles if one.key not in mine), *own.roles]
    accepted, problems = tolerant(code, candidates)
    catalog = built(code, accepted) if accepted else code
    return Composition(
        catalog=catalog,
        problems={**shadowed, **problems},
        own=frozenset(one.key for one in accepted if one.key in mine),
    )
