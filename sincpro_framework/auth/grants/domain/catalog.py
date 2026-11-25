"""Grants: the roles a service declares, what each grants, how they are grouped for a person to
choose, the shape of Odoo 20's groups, privileges and categories. Who holds which is
`assignments`.

    accounting = Category("accounting", "Accounting")
    invoicing = Privilege("invoicing", "Invoicing", category=accounting, placeholder="No access")
    clerk = Role("billing.clerk", "Clerk", grants={P.READ}, privilege=invoicing, sequence=10)
    manager = Role("billing.manager", "Manager", grants={P.ISSUE}, implies=(clerk,),
                   privilege=invoicing, sequence=20)
    portal = Role("portal", "Portal", disjoint={"billing.clerk"})
    catalog = GrantCatalog([manager, portal],
                           entitlements=[Entitlement("e-invoicing", gates={P.ISSUE})])

    catalog.permissions_of({"billing.manager"})   ->  {P.READ, P.ISSUE}
    catalog.describe()                             ->  categories → privileges → roles

Context: the catalog is code — like `Permission`, reviewed and versioned with the use cases that
require what it grants; who holds which role is data, in a `GrantStore`. A store keeps only what
was assigned — the closure is computed (Odoo 20's `group_ids` beside `all_group_ids`), so a role
that changes what it implies changes every holder at once. A privilege's roles are its exclusive
levels, each implying the one below: a person holds one level of each privilege. Disjoint roles
are never held together, judged on the closure — a role that implies a disjoint one conflicts
too. An entitlement gates permissions behind what a tenant bought: without it, a permission it
gates is never granted, whoever holds the role.
"""

from collections.abc import Iterable, Mapping, Set
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Protocol

from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.sincpro_abstractions import DataTransferObject


@dataclass(frozen=True)
class Category:
    """A section of the screen where roles are chosen — Odoo's `ir.module.category`."""

    key: str
    name: str
    sequence: int = 0


@dataclass(frozen=True)
class Privilege:
    """One choice on that screen: which level of it a person holds, or none (`placeholder`) —
    Odoo 20's `res.groups.privilege`."""

    key: str
    name: str
    category: Category | None = None
    placeholder: str = "No access"
    description: str = ""
    sequence: int = 0


@dataclass(frozen=True)
class Role:
    """What a role grants and which roles it implies — Odoo's group with its `implied_ids`.

    Role("billing.manager", "Manager", grants={P.ISSUE}, implies=(clerk,))
    """

    key: str
    name: str = ""
    grants: Set[str] = frozenset()
    implies: tuple["Role", ...] = ()
    privilege: Privilege | None = None
    sequence: int = 0
    """Its place among its privilege's levels — the higher, the more it holds."""
    disjoint: Set[str] = frozenset()
    """Roles never held together with this one — internal and portal."""
    description: str = ""

    def __post_init__(self) -> None:
        """`grants` and `disjoint` taken from any iterable, held as frozen sets."""
        object.__setattr__(self, "grants", frozenset(self.grants))
        object.__setattr__(self, "disjoint", frozenset(self.disjoint))


@dataclass(frozen=True)
class Entitlement:
    """What a tenant bought: the permissions it `gates` are granted only to its tenants."""

    key: str
    name: str = ""
    gates: Set[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        object.__setattr__(self, "gates", frozenset(self.gates))


class RoleView(DataTransferObject):
    """A role as an administration screen shows it."""

    key: str
    name: str
    description: str = ""
    privilege: str | None = None
    grants: list[str]
    implies: list[str]
    all_implied: list[str]
    all_implied_by: list[str]
    disjoint: list[str]


class PrivilegeView(DataTransferObject):
    """A privilege and its levels, from the least to the most."""

    key: str
    name: str
    description: str = ""
    category: str | None = None
    placeholder: str
    levels: list[str]


class CategoryView(DataTransferObject):
    """A category and its privileges, in order."""

    key: str
    name: str
    privileges: list[str]


class CatalogDescription(DataTransferObject):
    """The whole catalog for a screen — Odoo 20's `view_group_hierarchy`: categories →
    privileges → roles, and the roles that belong to no privilege."""

    categories: list[CategoryView]
    privileges: dict[str, PrivilegeView]
    roles: dict[str, RoleView]
    other_roles: list[str]
    entitlements: dict[str, list[str]]


class RolePermissionsLike(Protocol):
    """What `from_role_permissions` reads of a `RolePermissions` table."""

    grants: dict[str, frozenset[str]]
    implies: dict[str, frozenset[str]]


def _every_role(roots: Iterable[Role]) -> dict[str, Role]:
    """Every role reachable from `roots` through `implies`, by key — `ProgrammingError` when
    two different roles share a key."""
    found: dict[str, Role] = {}
    pending = list(roots)
    while pending:
        role = pending.pop()
        known = found.get(role.key)
        if known is not None:
            if known != role:
                raise exceptions.ProgrammingError(f"two roles are declared as {role.key!r}")
            continue
        found[role.key] = role
        pending.extend(role.implies)
    return found


@dataclass(frozen=True)
class Composition:
    """A tenant's catalog as composed: the catalog in effect, the roles defined at runtime left
    out with why, and the keys the tenant defines itself."""

    catalog: "GrantCatalog"
    problems: Mapping[str, str] = field(default_factory=dict)
    own: frozenset[str] = frozenset()
    """The keys defined for exactly this tenant — its own roles."""
    retired: frozenset[str] = frozenset()
    """The keys removed at runtime, or defined at runtime and declared by the code since — an
    assignment of one grants nothing, and none is given again."""


class CatalogSource(Protocol):
    """What answers the catalog in effect for a tenant — a `GrantCatalog` itself, or a
    `LiveCatalog` with the roles defined at runtime beside it."""

    def in_effect(self, tenant: str | None = None) -> Composition:
        """The composition to resolve `tenant`'s identities with — what a replica holds now."""
        ...

    def latest(self, tenant: str | None = None) -> Composition:
        """The tenant's catalog read from the source now — what an administration checks a
        change against."""
        ...


class GrantCatalog:
    """The roles a service declares, their closure, their levels and what they grant."""

    def __init__(
        self, roles: Iterable[Role], entitlements: Iterable[Entitlement] = ()
    ) -> None:
        """Every role given and every role they imply; checked once:

        1. Two roles of one key, or a disjoint role nobody declared: `ProgrammingError`.
        2. The closure of every role — itself and what it implies, transitively; a cycle ends.
        3. A privilege's levels, ordered by `sequence`, each implying the one below; a level
           that does not is a `ProgrammingError` — one level must hold the ones under it.
        Final: entitlements by key.
        """
        self.roles = _every_role(roles)
        for role in self.roles.values():
            unknown = role.disjoint - self.roles.keys()
            if unknown:
                raise exceptions.ProgrammingError(
                    f"{role.key} is disjoint from roles nobody declared: {sorted(unknown)}"
                )
        self._closure = {key: self._walk(key) for key in self.roles}
        self.levels: dict[str, list[Role]] = {}
        for role in sorted(self.roles.values(), key=lambda one: (one.sequence, one.key)):
            if role.privilege is not None:
                self.levels.setdefault(role.privilege.key, []).append(role)
        for privilege, levels in self.levels.items():
            for lower, higher in zip(levels, levels[1:]):
                if lower.key not in self._closure[higher.key]:
                    raise exceptions.ProgrammingError(
                        f"privilege {privilege}: {higher.key} does not imply {lower.key}, the "
                        "level below it"
                    )
        self.entitlements = {one.key: one for one in entitlements}
        self._cached = lru_cache(maxsize=4096)(self._permissions_of)

    def _walk(self, key: str) -> frozenset[str]:
        """`key` and every role it implies, transitively — a worklist, so a cycle ends."""
        held: set[str] = set()
        pending = [key]
        while pending:
            current = pending.pop()
            if current not in held:
                held.add(current)
                pending.extend(one.key for one in self.roles[current].implies)
        return frozenset(held)

    def in_effect(self, tenant: str | None = None) -> Composition:
        """Itself, for every tenant — a catalog of code is in effect as declared; a
        `LiveCatalog` answers the one with the roles defined at runtime."""
        return Composition(catalog=self)

    def latest(self, tenant: str | None = None) -> Composition:
        """Itself, with nothing defined at runtime."""
        return Composition(catalog=self)

    def role(self, key: str) -> Role:
        """The role `key` — `InvalidGrant` when the catalog does not declare it."""
        if key not in self.roles:
            raise exceptions.InvalidGrant(f"{key} is not a role")
        return self.roles[key]

    def closure(self, roles: Iterable[str]) -> frozenset[str]:
        """The roles held through `roles` — each and what it implies. A key the catalog does
        not declare stays, granting nothing: a token's own role a screen may read."""
        return frozenset().union(*(self._closure.get(one, frozenset({one})) for one in roles))

    def _permissions_of(self, roles: frozenset[str]) -> frozenset[str]:
        """What the closure of `roles` grants — cached per set of roles, Odoo's ormcache."""
        return frozenset().union(
            *(self.roles[one].grants for one in self.closure(roles) if one in self.roles)
        )

    def permissions_of(self, roles: Iterable[str]) -> frozenset[str]:
        """What `roles` grant, through every role they imply.

        catalog.permissions_of({"billing.manager"})   ->  {P.READ, P.ISSUE}
        """
        return self._cached(frozenset(roles))

    def gated(
        self, permissions: frozenset[str], entitlements: Iterable[str]
    ) -> frozenset[str]:
        """`permissions` without those gated by an entitlement the tenant does not have."""
        held = set(entitlements)
        withheld = frozenset().union(
            *(one.gates for key, one in self.entitlements.items() if key not in held)
        )
        return permissions - withheld

    def conflicts(self, roles: Iterable[str]) -> list[str]:
        """Why `roles` cannot be held together — two disjoint roles in their closure, two levels
        of one privilege assigned. Empty when they can.

            catalog.conflicts({"portal", "billing.manager"})
                ->  ["portal and billing.clerk are never held together"]
        """
        assigned = set(roles)
        held = self.closure(assigned)
        found = sorted(
            {
                f"{min(one, other)} and {max(one, other)} are never held together"
                for one in held
                if one in self.roles
                for other in self.roles[one].disjoint
                if other in held
            }
        )
        for privilege, levels in self.levels.items():
            given = [one.key for one in levels if one.key in assigned]
            if len(given) > 1:
                found.append(f"{privilege} is given at several levels: {', '.join(given)}")
        return found

    def level_of(self, privilege: str, roles: Iterable[str]) -> Role | None:
        """The highest level of `privilege` held through `roles` — `None` for no access."""
        held = self.closure(roles)
        found = [one for one in self.levels.get(privilege, []) if one.key in held]
        return found[-1] if found else None

    def describe(self) -> CatalogDescription:
        """The catalog as an administration screen draws it."""
        implied_by: dict[str, set[str]] = {key: set() for key in self.roles}
        for key, closure in self._closure.items():
            for other in closure - {key}:
                implied_by[other].add(key)
        privileges = {
            levels[0].privilege.key: levels[0].privilege
            for levels in self.levels.values()
            if levels[0].privilege is not None
        }
        categories: dict[str, tuple[Category, list[str]]] = {}
        for privilege in sorted(privileges.values(), key=lambda one: (one.sequence, one.key)):
            if privilege.category is not None:
                entry = categories.setdefault(
                    privilege.category.key, (privilege.category, [])
                )
                entry[1].append(privilege.key)
        return CatalogDescription(
            categories=[
                CategoryView(key=category.key, name=category.name, privileges=keys)
                for category, keys in sorted(
                    categories.values(), key=lambda one: (one[0].sequence, one[0].key)
                )
            ],
            privileges={
                key: PrivilegeView(
                    key=key,
                    name=privilege.name,
                    description=privilege.description,
                    category=privilege.category.key if privilege.category else None,
                    placeholder=privilege.placeholder,
                    levels=[one.key for one in self.levels[key]],
                )
                for key, privilege in privileges.items()
            },
            roles={
                key: RoleView(
                    key=key,
                    name=role.name or key,
                    description=role.description,
                    privilege=role.privilege.key if role.privilege else None,
                    grants=sorted(role.grants),
                    implies=sorted(one.key for one in role.implies),
                    all_implied=sorted(self._closure[key] - {key}),
                    all_implied_by=sorted(implied_by[key]),
                    disjoint=sorted(role.disjoint),
                )
                for key, role in sorted(self.roles.items())
            },
            other_roles=sorted(
                key for key, role in self.roles.items() if role.privilege is None
            ),
            entitlements={
                key: sorted(one.gates) for key, one in sorted(self.entitlements.items())
            },
        )

    @classmethod
    def from_role_permissions(cls, table: "RolePermissionsLike") -> "GrantCatalog":
        """A catalog out of a `RolePermissions` table — the same roles, grants and implications."""
        keys = (
            set(table.grants)
            | set(table.implies)
            | {one for more in table.implies.values() for one in more}
        )
        built: dict[str, Role] = {}

        def build(key: str, path: frozenset[str]) -> Role:
            if key not in built:
                implied = tuple(
                    build(one, path | {key})
                    for one in sorted(table.implies.get(key, ()))
                    if one not in path
                )
                built[key] = Role(
                    key, grants=table.grants.get(key, frozenset()), implies=implied
                )
            return built[key]

        return cls([build(key, frozenset()) for key in sorted(keys)])
