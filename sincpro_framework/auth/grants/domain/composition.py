"""The catalog in effect for a scope, composed from the code's and what is defined at runtime —
pure, no store: what `LiveCatalog` reads with and `RoleAdministration` checks a change against.

    built(code, [auditor])               ->  a catalog, or `InvalidGrant` saying why not
    compose(code, shared, own, "bo")     ->  Composition(catalog, problems, own)

Context: a tenant's roles are held by their catalog key (`cash.desk@bo`), so they never meet the
code's nor every tenant's. A change written never leaves another role broken
(`RoleAdministration`), so the roles stored build at once — one catalog, one pass. When one does
not — the code dropped a role it implies, the platform removed one every tenant shared — it
grants nothing and the others still do, decided the same way on every replica.
"""

from collections.abc import Iterable, Sequence

from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.grants.domain.catalog import (
    Composition,
    GrantCatalog,
    Privilege,
    Role,
)
from sincpro_framework.auth.grants.domain.definitions import (
    DefinedRoles,
    RoleDefinition,
    catalog_key,
)


def _privileges_of(code: GrantCatalog) -> dict[str, Privilege]:
    """The privileges the code's roles name, by key."""
    return {
        role.privilege.key: role.privilege
        for role in code.roles.values()
        if role.privilege is not None
    }


def built(code: GrantCatalog, defined: Sequence[RoleDefinition]) -> GrantCatalog:
    """The code's catalog with `defined` beside it, by catalog key — `InvalidGrant` saying why
    it cannot hold.

    1. A defined role whose catalog key the code declares, or two of one catalog key: refused.
    2. Each defined role built once, what it implies first; a cycle, an unknown role or an
       unknown privilege: refused.
    3. One catalog checked as the code's is — disjoint roles, exclusive levels — with the
       code's entitlements.
    Final: no defined role holds together, through what it implies, what may never be held
    together.
    """
    by_key: dict[str, RoleDefinition] = {}
    for one in defined:
        if one.catalog_key in code.roles:
            raise exceptions.InvalidGrant(
                f"{one.catalog_key}: declared in code, changed there"
            )
        if one.catalog_key in by_key:
            raise exceptions.InvalidGrant(f"{one.catalog_key} is defined twice")
        by_key[one.catalog_key] = one
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
            name=definition.name or definition.key,
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
        catalog = GrantCatalog(
            [*code.roles.values(), *roles], entitlements=code.entitlements.values()
        )
    except exceptions.ProgrammingError as refused:
        raise exceptions.InvalidGrant(str(refused)) from refused
    for key in sorted(by_key):
        conflicts = catalog.conflicts({key})
        if conflicts:
            raise exceptions.InvalidGrant(f"{key} could never be held: {conflicts[0]}")
    return catalog


def tolerant(
    code: GrantCatalog, defined: Iterable[RoleDefinition]
) -> tuple[list[RoleDefinition], dict[str, str]]:
    """The defined roles that build with the code's — and the others, by catalog key, with why.
    All at once when they build together; else one at a time in catalog-key order, a role
    waiting for what it implies — the same answer on every replica."""
    pending = sorted(defined, key=lambda one: one.catalog_key)
    try:
        built(code, pending)
        return pending, {}
    except exceptions.InvalidGrant:
        pass
    accepted: list[RoleDefinition] = []
    why: dict[str, str] = {}
    moved = True
    while pending and moved:
        moved = False
        for one in list(pending):
            try:
                built(code, [*accepted, one])
            except exceptions.InvalidGrant as refused:
                why[one.catalog_key] = str(refused)
                continue
            accepted.append(one)
            pending.remove(one)
            moved = True
    return accepted, {one.catalog_key: why[one.catalog_key] for one in pending}


def compose(
    code: GrantCatalog, shared: DefinedRoles, own: DefinedRoles, tenant: str | None
) -> Composition:
    """The catalog of `tenant`: the code's, what every tenant shares, and its own — the
    definitions that do not build left out, with why; `own` the catalog keys of its own roles
    that do; `retired` the keys no assignment grants — removed at runtime, or defined at runtime
    and declared by the code since, whose assignments were given for the runtime role."""
    mine = own.roles if tenant is not None else ()
    accepted, problems = tolerant(code, [*shared.roles, *mine])
    retired = {catalog_key(key, None) for key in shared.retired}
    if tenant is not None:
        retired |= {catalog_key(key, tenant) for key in own.retired}
    retired |= {
        one.catalog_key for one in [*shared.roles, *mine] if one.catalog_key in code.roles
    }
    for key in sorted(retired & code.roles.keys()):
        problems[key] = (
            "declared in code, and used at runtime before: no assignment grants it — rename it "
            "in code"
        )
    return Composition(
        catalog=built(code, accepted) if accepted else code,
        problems=problems,
        own=frozenset(one.catalog_key for one in accepted if one.tenant is not None),
        retired=frozenset(retired),
    )
