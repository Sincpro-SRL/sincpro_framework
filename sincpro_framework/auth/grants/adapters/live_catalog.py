"""`LiveCatalog`: the catalog in effect — the roles the code declares and the ones defined at
runtime, checked by the same rules, current on every replica.

    live = LiveCatalog(catalog, KeyValueRoleDefinitions(), permissions=BillingPermission)
    GrantResolver(live, grants)                  # resolves with the roles defined at runtime
    GrantAdministration(live, grants, ...).define_role(RoleDefinition(key="billing.auditor", ...))

Context: a replica reads the version of what is defined at most once each `check_every` (a
second) and builds the catalog again only when it changed — a role defined holds within that
time everywhere. What is defined only ever adds: a role of the code is never redefined, a
permission no code declares is never granted. A stored role that no longer builds — the code
dropped a role it implies — grants nothing: the code's catalog stands alone, and the reason is
logged. A store that does not answer leaves the last catalog read in effect.
"""

import threading
import time
from collections.abc import Callable, Iterable, Sequence
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

    1. A defined role named like one of the code: refused — the code's is changed in code.
    2. Each defined role built once, what it implies first; a cycle, an unknown role or an
       unknown privilege: refused.
    3. Final: one catalog checked as the code's is — disjoint roles, exclusive levels — with the
       code's entitlements.
    """
    by_key = {one.key: one for one in defined}
    clash = sorted(by_key.keys() & code.roles.keys())
    if clash:
        raise exceptions.InvalidGrant(f"{', '.join(clash)}: declared in code, changed there")
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


class LiveCatalog:
    """The code's catalog and the roles `definitions` keeps — what a resolver and an
    administration ask with `current()`."""

    def __init__(
        self,
        catalog: GrantCatalog,
        definitions: RoleDefinitions,
        permissions: Iterable[str] | None = None,
        check_every: timedelta = timedelta(seconds=1),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """`permissions` is what a defined role may grant — every value of the project's
        `Permission` classes; what the code's roles grant when not given."""
        self.code = catalog
        self.definitions = definitions
        self.permissions = frozenset(
            permissions
            if permissions is not None
            else set().union(*(role.grants for role in catalog.roles.values()))
        )
        self.check_every = check_every
        self.clock = clock
        self._lock = threading.Lock()
        self._version = 0
        self._catalog = catalog
        self._checked_at: float | None = None

    def _refreshed(self, defined: DefinedRoles) -> None:
        """Build again from `defined` — the code's catalog alone when it no longer builds."""
        try:
            self._catalog = built(self.code, defined.roles) if defined.roles else self.code
        except exceptions.InvalidGrant as broken:
            logger.error(
                f"the roles defined at runtime no longer build — they grant nothing: {broken}"
            )
            self._catalog = self.code
        self._version = defined.version

    def current(self) -> GrantCatalog:
        """The catalog in effect — read again once `check_every` passed and the version moved."""
        now = self.clock()
        with self._lock:
            if self._checked_at is not None and now - self._checked_at < (
                self.check_every.total_seconds()
            ):
                return self._catalog
            self._checked_at = now
            try:
                defined = self.definitions.read()
            except StoreUnavailable:
                logger.warning(
                    "the roles defined at runtime could not be read: kept as they were"
                )
                return self._catalog
            if defined.version != self._version:
                self._refreshed(defined)
            return self._catalog

    def defined(self) -> tuple[RoleDefinition, ...]:
        """The roles defined at runtime — what a screen lists as editable."""
        return self.definitions.read().roles

    def _write(self, change: Callable[[DefinedRoles], list[RoleDefinition]]) -> None:
        """Apply `change` to what is defined and write it — read again and retried while
        another administrator wrote first."""
        for _ in range(5):
            defined = self.definitions.read()
            roles = change(defined)
            built(self.code, roles)
            if self.definitions.write(defined.version, roles):
                with self._lock:
                    self._refreshed(
                        DefinedRoles(version=defined.version + 1, roles=tuple(roles))
                    )
                return
        raise exceptions.InvalidGrant(
            "the roles changed under this write five times: try again"
        )

    def define(self, definition: RoleDefinition) -> bool:
        """Keep `definition`, new or replacing one defined before — `True` when it is new.

        1. A permission no code declares, granted: `InvalidGrant`.
        2. The catalog with it does not hold (a role of the code's name, a cycle, an unknown
           role or privilege, two levels that do not imply each other): `InvalidGrant`.
        Final: written over the version read; this replica's catalog current at once.
        """
        unknown = sorted(definition.grants - self.permissions)
        if unknown:
            raise exceptions.InvalidGrant(
                f"{definition.key} grants {', '.join(unknown)}, which no code declares"
            )
        new = [True]

        def replaced(defined: DefinedRoles) -> list[RoleDefinition]:
            others = [one for one in defined.roles if one.key != definition.key]
            new[0] = len(others) == len(defined.roles)
            return [*others, definition]

        self._write(replaced)
        return new[0]

    def remove(self, key: str) -> None:
        """Drop the role `key` defined at runtime — `InvalidGrant` for one of the code, one not
        defined, or one another defined role implies. Its holders keep an assignment that grants
        nothing."""
        if key in self.code.roles:
            raise exceptions.InvalidGrant(f"{key}: declared in code, changed there")

        def dropped(defined: DefinedRoles) -> list[RoleDefinition]:
            if key not in {one.key for one in defined.roles}:
                raise exceptions.InvalidGrant(f"{key} is not a role defined at runtime")
            implying = sorted(one.key for one in defined.roles if key in one.implies)
            if implying:
                raise exceptions.InvalidGrant(f"{key} is implied by {', '.join(implying)}")
            return [one for one in defined.roles if one.key != key]

        self._write(dropped)
