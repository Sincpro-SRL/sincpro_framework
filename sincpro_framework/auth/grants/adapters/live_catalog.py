"""`LiveCatalog`: the catalog in effect for a tenant — the roles the code declares, the ones
defined at runtime for every tenant, and the tenant's own — checked by the same rules, current on
every replica.

    live = LiveCatalog(catalog, KeyValueRoleDefinitions(), permissions=BillingPermission)
    GrantResolver(live, grants)                       # each identity in its tenant's catalog
    GrantAdministration(live, grants, ...).define_role(RoleDefinition(key="cash.supervisor",
                                                                      tenant="bo", ...))

Context: a replica reads the versions of a tenant's scopes at most once each `check_every` (a
second), and builds that tenant's catalog again only when one moved — a role defined holds
within that time everywhere; on the replica that wrote it, at once. What is defined only ever
adds: a role of the code is never redefined, a permission no code declares is never granted, a
tenant's role never reaches another tenant. A defined role that no longer builds — the code
dropped a role it implies — grants nothing, the others still do; `problems()` says which and why.
A store that does not answer leaves the last catalog read in effect.
"""

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import timedelta

from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.grants.domain.catalog import GrantCatalog, Privilege, Role
from sincpro_framework.auth.grants.domain.definitions import (
    DefinedRoles,
    RoleDefinition,
    RoleDefinitions,
)
from sincpro_framework.data_layer.store import StoreUnavailable
from sincpro_framework.sincpro_logger import logger


def _privileges_of(catalog: GrantCatalog) -> dict[str, Privilege]:
    """The privileges the code's roles name, by key."""
    return {
        role.privilege.key: role.privilege
        for role in catalog.roles.values()
        if role.privilege is not None
    }


def built(code: GrantCatalog, defined: Sequence[RoleDefinition]) -> GrantCatalog:
    """The code's catalog with `defined` beside it — `InvalidGrant` saying why it cannot hold.

        built(catalog, [RoleDefinition(key="billing.auditor", implies={"billing.clerk"})])
            ->  a catalog where billing.auditor holds billing.clerk's grants too

    1. A defined role named like one of the code, or two defined of one name: refused.
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
    code: GrantCatalog, defined: Sequence[RoleDefinition]
) -> tuple[list[RoleDefinition], dict[str, str]]:
    """The defined roles that build with the code's, in the order they were given — and the
    others, with why. A role waits for what it implies, so the order defined does not matter.
    """
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
            why.pop(one.key, None)
            moved = True
    return accepted, {one.key: why[one.key] for one in pending}


@dataclass
class _Scope:
    """One tenant's catalog as last built, and the versions it was built from."""

    versions: tuple[int, int] = (-1, -1)
    catalog: GrantCatalog | None = None
    problems: dict[str, str] = field(default_factory=dict)
    checked_at: float | None = None


class LiveCatalog:
    """The code's catalog with the roles `definitions` keeps — what a resolver and an
    administration ask with `current(tenant)`."""

    def __init__(
        self,
        catalog: GrantCatalog,
        definitions: RoleDefinitions,
        permissions: Iterable[str] | None = None,
        check_every: timedelta = timedelta(seconds=1),
        clock: Callable[[], float] = time.monotonic,
        tenants_kept: int = 4096,
    ) -> None:
        """`permissions` is what a defined role may grant — every value of the project's
        `Permission` classes; what the code's roles grant when not given. `tenants_kept` bounds
        how many tenants' catalogs a replica holds at once."""
        self.code = catalog
        self.definitions = definitions
        self.permissions = frozenset(
            permissions
            if permissions is not None
            else set().union(*(role.grants for role in catalog.roles.values()))
        )
        self.check_every = check_every
        self.clock = clock
        self.tenants_kept = tenants_kept
        self._lock = threading.Lock()
        self._scopes: OrderedDict[str | None, _Scope] = OrderedDict()

    def _scope(self, tenant: str | None) -> _Scope:
        """The cached scope of `tenant`, the oldest let go past `tenants_kept`."""
        scope = self._scopes.pop(tenant, None) or _Scope()
        self._scopes[tenant] = scope
        while len(self._scopes) > self.tenants_kept:
            self._scopes.popitem(last=False)
        return scope

    def _read(self, tenant: str | None) -> tuple[DefinedRoles, DefinedRoles]:
        """What every tenant shares and what `tenant` adds — nothing added for `None`."""
        shared = self.definitions.read(None)
        own = DefinedRoles() if tenant is None else self.definitions.read(tenant)
        return shared, own

    def _rebuilt(self, scope: _Scope, shared: DefinedRoles, own: DefinedRoles) -> None:
        """Build `scope` from what is defined — each role that no longer builds left out."""
        accepted, problems = tolerant(self.code, [*shared.roles, *own.roles])
        for key, why in problems.items():
            logger.error(f"the role {key} defined at runtime grants nothing: {why}")
        scope.catalog = built(self.code, accepted) if accepted else self.code
        scope.problems = problems
        scope.versions = (shared.version, own.version)

    def _refreshed(self, tenant: str | None) -> _Scope:
        """`tenant`'s scope, read again once `check_every` passed and built again only when a
        version moved — the last one kept while the store does not answer."""
        now = self.clock()
        scope = self._scope(tenant)
        if scope.checked_at is not None and now - scope.checked_at < (
            self.check_every.total_seconds()
        ):
            return scope
        scope.checked_at = now
        try:
            shared, own = self._read(tenant)
        except StoreUnavailable:
            logger.warning(
                "the roles defined at runtime could not be read: kept as they were"
            )
            return scope
        if (shared.version, own.version) != scope.versions:
            self._rebuilt(scope, shared, own)
        return scope

    def current(self, tenant: str | None = None) -> GrantCatalog:
        """The catalog in effect for `tenant` — what every tenant shares for `None`."""
        with self._lock:
            return self._refreshed(tenant).catalog or self.code

    def problems(self, tenant: str | None = None) -> dict[str, str]:
        """The roles defined for `tenant` that grant nothing because they no longer build, and
        why — what an administration screen shows to be fixed."""
        with self._lock:
            return dict(self._refreshed(tenant).problems)

    def defined(self, tenant: str | None = None) -> tuple[RoleDefinition, ...]:
        """The roles defined for exactly this scope — what a screen lists as editable."""
        return self.definitions.read(tenant).roles

    def _write(
        self,
        tenant: str | None,
        change: Callable[[DefinedRoles, list[RoleDefinition]], list[RoleDefinition]],
    ) -> None:
        """Apply `change` to the scope and write it — handed the roles of every tenant that
        build, read again and retried while another administrator wrote first."""
        for _ in range(5):
            shared, own = self._read(tenant)
            working, _ = tolerant(self.code, [*shared.roles, *own.roles])
            mine = own if tenant is not None else shared
            roles = change(mine, working)
            if self.definitions.write(tenant, mine.version, roles):
                with self._lock:
                    if tenant is None:
                        self._scopes.clear()
                    else:
                        self._scopes.pop(tenant, None)
                return
        raise exceptions.InvalidGrant(
            "the roles changed under this write five times: try again"
        )

    def define(self, definition: RoleDefinition) -> bool:
        """Keep `definition` in its scope, new or replacing one defined before — `True` when new.

        1. A permission no code declares, granted: `InvalidGrant`.
        2. A tenant's role named like one every tenant shares: `InvalidGrant`.
        3. The catalog with it does not hold (a role of the code's name, a cycle, an unknown
           role or privilege, two levels that do not imply each other): `InvalidGrant`.
        Final: written over the version read; this replica's catalog current at once.
        """
        unknown = sorted(definition.grants - self.permissions)
        if unknown:
            raise exceptions.InvalidGrant(
                f"{definition.key} grants {', '.join(unknown)}, which no code declares"
            )
        tenant = definition.tenant
        new = [True]

        def replaced(
            mine: DefinedRoles, working: list[RoleDefinition]
        ) -> list[RoleDefinition]:
            if tenant is not None and any(
                one.key == definition.key and one.tenant is None for one in working
            ):
                raise exceptions.InvalidGrant(
                    f"{definition.key} is a role every tenant shares: name this one otherwise"
                )
            others = [one for one in working if one.key != definition.key]
            built(self.code, [*others, definition])
            kept = [one for one in mine.roles if one.key != definition.key]
            new[0] = len(kept) == len(mine.roles)
            return [*kept, definition]

        self._write(tenant, replaced)
        return new[0]

    def remove(self, key: str, tenant: str | None = None) -> RoleDefinition:
        """Drop the role `key` defined for `tenant` — `InvalidGrant` for one of the code, one not
        defined there, or one another role of the scope implies. Its holders keep an assignment
        that grants nothing. Answers what was removed."""
        if key in self.code.roles:
            raise exceptions.InvalidGrant(f"{key}: declared in code, changed there")
        removed: list[RoleDefinition] = []

        def dropped(
            mine: DefinedRoles, working: list[RoleDefinition]
        ) -> list[RoleDefinition]:
            found = [one for one in mine.roles if one.key == key]
            if not found:
                raise exceptions.InvalidGrant(f"{key} is not a role defined for this scope")
            implying = sorted(one.key for one in mine.roles if key in one.implies)
            if implying:
                raise exceptions.InvalidGrant(f"{key} is implied by {', '.join(implying)}")
            removed[:] = found
            return [one for one in mine.roles if one.key != key]

        self._write(tenant, dropped)
        return removed[0]
