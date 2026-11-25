"""`CatalogBuilder`: the catalog in effect for a scope, built from the code's and the roles defined
at runtime — pure, no store: what `TenantCatalogs` reads with and `RoleAdministration` checks a
change against.

    builder = CatalogBuilder(code)
    builder.build([auditor])                  ->  a catalog, or `InvalidGrant` saying why not
    builder.build_valid(defined)              ->  the roles that build, and why the others do not
    builder.compose(shared, own, "bo")        ->  EffectiveCatalog(catalog, problems, own, retired)

Context: a tenant's roles are held by their catalog key (`cash.desk@bo`), so they never meet the
code's nor every tenant's. A change written never leaves another role broken
(`RoleAdministration`), so the roles stored build at once — one catalog, one pass. When one does
not — the code dropped a role it implies, the platform removed one every tenant shared — it
grants nothing and the others still do, decided the same way on every replica: the roles are
tried one at a time in topological order (each after the roles it implies; Kahn, ties by catalog
key), each built once.
"""

import heapq
from collections.abc import Iterable, Sequence

from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.grants.domain.catalog import (
    EffectiveCatalog,
    GrantCatalog,
    Role,
)
from sincpro_framework.auth.grants.domain.definitions import (
    DefinedRoles,
    RoleDefinition,
    catalog_key,
)


class CatalogBuilder:
    """Catalogs of `code` with roles defined at runtime beside it."""

    def __init__(self, code: GrantCatalog) -> None:
        """`code` — the roles the code declares; the privileges they name, by key."""
        self.code = code
        self.privileges = {
            role.privilege.key: role.privilege
            for role in code.roles.values()
            if role.privilege is not None
        }

    def build(self, defined: Sequence[RoleDefinition]) -> GrantCatalog:
        """The code's catalog with `defined` beside it, by catalog key — `InvalidGrant` saying
        why it cannot hold.

        1. A defined role whose catalog key the code declares, or two of one catalog key:
           refused.
        2. Each defined role built once, what it implies first; a cycle, an unknown role or an
           unknown privilege: refused.
        3. One catalog checked as the code's is — disjoint roles, exclusive levels — with the
           code's entitlements.
        Final: no defined role holds together, through what it implies, what may never be held
        together.
        """
        code = self.code
        by_key: dict[str, RoleDefinition] = {}
        for one in defined:
            if one.catalog_key in code.roles:
                raise exceptions.InvalidGrant(
                    f"{one.catalog_key}: declared in code, changed there"
                )
            if one.catalog_key in by_key:
                raise exceptions.InvalidGrant(f"{one.catalog_key} is defined twice")
            by_key[one.catalog_key] = one
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
            if (
                definition.privilege is not None
                and definition.privilege not in self.privileges
            ):
                raise exceptions.InvalidGrant(f"{definition.privilege} is not a privilege")
            made[key] = Role(
                key=key,
                name=definition.name or definition.key,
                grants=definition.grants,
                implies=tuple(make(one, path | {key}) for one in sorted(definition.implies)),
                privilege=(
                    None
                    if definition.privilege is None
                    else self.privileges[definition.privilege]
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

    def order(self, defined: Sequence[RoleDefinition]) -> list[RoleDefinition]:
        """`defined` sorted so each role comes after the defined roles it implies — Kahn's
        sort, the lowest catalog key first among the free ones; the roles in a cycle, or behind
        one, last in the order given.

            order([manager (implies clerk), clerk])   ->  [clerk, manager]
        """
        keys = {one.catalog_key for one in defined}
        waiting = [len(one.implies & keys) for one in defined]
        dependents: dict[str, list[int]] = {}
        for index, one in enumerate(defined):
            for key in one.implies & keys:
                dependents.setdefault(key, []).append(index)
        free = [
            (one.catalog_key, index)
            for index, one in enumerate(defined)
            if not waiting[index]
        ]
        heapq.heapify(free)
        placed: list[int] = []
        released: set[str] = set()
        while free:
            key, index = heapq.heappop(free)
            placed.append(index)
            if key in released:
                continue
            released.add(key)
            for dependent in dependents.get(key, ()):
                waiting[dependent] -= 1
                if not waiting[dependent]:
                    heapq.heappush(free, (defined[dependent].catalog_key, dependent))
        ordered = set(placed)
        left = [index for index in range(len(defined)) if index not in ordered]
        return [defined[index] for index in [*placed, *left]]

    def _is_buildable_alone(self, one: RoleDefinition, accepted: set[str]) -> bool:
        """Whether `one` can build beside the `accepted` catalog keys as far as it alone says
        — a key of its own, each role it implies known, its privilege the code's; what only the
        whole catalog tells (disjoint roles, levels) is left to `build`."""
        return (
            one.catalog_key not in self.code.roles
            and one.catalog_key not in accepted
            and all(key in self.code.roles or key in accepted for key in one.implies)
            and (one.privilege is None or one.privilege in self.privileges)
        )

    def build_valid(
        self, defined: Iterable[RoleDefinition]
    ) -> tuple[list[RoleDefinition], dict[str, str]]:
        """The defined roles that build with the code's — and the others, by catalog key, with
        why — the same answer on every replica.

        1. All at once, in catalog-key order: when they build together, all of them.
        2. In `order` — each after what it implies — the ones `_is_buildable_alone` beside the
           ones before them, built together once: when they build, those; each one refused
           built with them alone, for the reason `build` gives.
        Final: else one at a time in `order`, a role accepted when it builds with the ones
        accepted before it.
        """
        pending = sorted(defined, key=lambda one: one.catalog_key)
        try:
            self.build(pending)
            return pending, {}
        except exceptions.InvalidGrant:
            pass
        ordered = self.order(pending)
        candidates: list[RoleDefinition] = []
        known: set[str] = set()
        for one in ordered:
            if self._is_buildable_alone(one, known):
                candidates.append(one)
                known.add(one.catalog_key)
        problems: dict[str, str] = {}
        try:
            self.build(candidates)
            accepted = candidates
        except exceptions.InvalidGrant:
            accepted = []
            for one in candidates:
                try:
                    self.build([*accepted, one])
                except exceptions.InvalidGrant as refused:
                    problems[one.catalog_key] = str(refused)
                    continue
                accepted.append(one)
        tried = {id(one) for one in candidates}
        for one in ordered:
            if id(one) in tried:
                continue
            try:
                self.build([*accepted, one])
            except exceptions.InvalidGrant as refused:
                problems[one.catalog_key] = str(refused)
            else:
                problems[one.catalog_key] = "it builds only after a role refused before it"
        return accepted, problems

    def compose(
        self, shared: DefinedRoles, own: DefinedRoles, tenant: str | None
    ) -> EffectiveCatalog:
        """The catalog of `tenant`: the code's, what every tenant shares, and its own — the
        definitions that do not build left out, with why; `own` the catalog keys of its own
        roles that do; `retired` the keys no assignment grants — removed at runtime, or defined
        at runtime and declared by the code since, whose assignments were given for the runtime
        role."""
        code = self.code
        mine = own.roles if tenant is not None else ()
        accepted, problems = self.build_valid([*shared.roles, *mine])
        retired = {catalog_key(key, None) for key in shared.retired}
        if tenant is not None:
            retired |= {catalog_key(key, tenant) for key in own.retired}
        retired |= {
            one.catalog_key for one in [*shared.roles, *mine] if one.catalog_key in code.roles
        }
        for key in sorted(retired & code.roles.keys()):
            problems[key] = (
                "declared in code, and used at runtime before: no assignment grants it — "
                "rename it in code"
            )
        return EffectiveCatalog(
            catalog=self.build(accepted) if accepted else code,
            problems=problems,
            own=frozenset(one.catalog_key for one in accepted if one.tenant is not None),
            retired=frozenset(retired),
        )
