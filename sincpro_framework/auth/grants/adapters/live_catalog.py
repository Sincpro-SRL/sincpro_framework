"""`LiveCatalog`: the catalog in effect for a tenant — the roles the code declares, the ones
defined at runtime for every tenant, and the tenant's own — what a resolver reads on every call.

    live = LiveCatalog(catalog, KeyValueRoleDefinitions(), permissions=BillingPermission)
    GrantResolver(live, grants)                     # each identity in its tenant's catalog
    RoleAdministration(live, grants, ...)           # what changes it

Context: it only reads. A replica reads a tenant's versions at most once each `check_every` (a
second), one caller per tenant, never holding back another tenant, and composes again only when
a version moved (`compose`: the tenant's own roles win, a role that does not build grants
nothing, the others still do). `latest(tenant)` reads now, past the cache — what an
administration checks a change against. A store that does not answer leaves the last catalog
composed in effect; a tenant never composed is the code's catalog until the store answers.
"""

import time
from collections.abc import Callable, Iterable
from datetime import timedelta
from typing import Any

from sincpro_framework.auth.grants.domain.catalog import Composition, GrantCatalog
from sincpro_framework.auth.grants.domain.composition import compose
from sincpro_framework.auth.grants.domain.definitions import (
    DefinedRoles,
    RoleDefinition,
    RoleDefinitions,
)
from sincpro_framework.auth.infrastructure.refreshed import Refreshed
from sincpro_framework.sincpro_logger import logger


class LiveCatalog:
    """The code's catalog with the roles `definitions` keeps, composed per tenant."""

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
        self._held: Refreshed[Composition] = Refreshed(
            read=self._read,
            build=self._composed,
            empty=(DefinedRoles(), DefinedRoles()),
            what="the roles defined at runtime",
            check_every=check_every,
            clock=clock,
            kept=tenants_kept,
        )

    def scopes(self, tenant: str | None) -> tuple[DefinedRoles, DefinedRoles]:
        """What every tenant shares and what `tenant` defines — read now, nothing for `None`."""
        shared = self.definitions.read(None)
        own = DefinedRoles() if tenant is None else self.definitions.read(tenant)
        return shared, own

    def _read(self, tenant: str | None) -> tuple[tuple[int, ...], Any]:
        """The versions and the documents of `tenant`'s scopes."""
        shared, own = self.scopes(tenant)
        return (shared.version, own.version), (shared, own)

    def _composed(self, documents: tuple[DefinedRoles, DefinedRoles]) -> Composition:
        """The composition of the documents, each role left out logged."""
        composition = compose(self.code, *documents)
        for key, why in composition.problems.items():
            logger.error(f"the role {key} defined at runtime grants nothing: {why}")
        return composition

    def current(self, tenant: str | None = None) -> GrantCatalog:
        """The catalog in effect for `tenant` — what every tenant shares for `None`."""
        return self._held.get(tenant).catalog

    def latest(self, tenant: str | None = None) -> Composition:
        """`tenant`'s catalog composed from the store now, past the cache."""
        return compose(self.code, *self.scopes(tenant))

    def problems(self, tenant: str | None = None) -> dict[str, str]:
        """The roles defined for `tenant` that grant nothing, and why — what a screen shows to
        be fixed."""
        return dict(self._held.get(tenant).problems)

    def defined(self, tenant: str | None = None) -> tuple[RoleDefinition, ...]:
        """The roles defined for exactly this scope — what a screen lists as editable."""
        return self.definitions.read(tenant).roles

    def stale(self, tenant: str | None = None) -> None:
        """Read `tenant`'s scopes again on the next call — every tenant's for `None`."""
        self._held.stale(tenant, everything=tenant is None)
