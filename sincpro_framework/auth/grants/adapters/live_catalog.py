"""`LiveCatalog`: the catalog in effect for a tenant — the roles the code declares, the ones
defined at runtime for every tenant, and the tenant's own — what a resolver reads on every call.

    live = LiveCatalog(catalog, KeyValueRoleDefinitions(), permissions=BillingPermission)
    GrantResolver(live, grants)                     # each identity in its tenant's catalog
    RoleAdministration(live, grants, ...)           # what changes it

Context: it only reads. A replica reads a tenant's versions at most once each `check_every` (a
second), one caller per tenant, never holding back another tenant, and composes again only when
a version moved. `latest(tenant)` reads now, past that wait — what an administration checks a
change against — composing again only for versions it has not composed yet. A store that does
not answer leaves the last catalog in effect; a tenant never composed is the code's catalog until
it answers — never more than the code grants.
"""

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable, Iterable
from datetime import timedelta
from typing import Any

from sincpro_framework.auth.domain.documents import fingerprint
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
        permissions: Iterable[str] | Callable[[], Iterable[str]] | None = None,
        check_every: timedelta = timedelta(seconds=1),
        clock: Callable[[], float] = time.monotonic,
        tenants_kept: int = 4096,
    ) -> None:
        """`permissions` is what a defined role may grant — every value of the project's
        `Permission` classes, or what answers them when asked; what the code's roles grant when
        not given. `tenants_kept` bounds how many tenants' catalogs a replica holds at once.
        """
        self.code = catalog
        self.definitions = definitions
        self._permissions = (
            permissions
            if permissions is not None
            else frozenset().union(*(role.grants for role in catalog.roles.values()))
        )
        self.tenants_kept = tenants_kept
        self._composed: OrderedDict[tuple[Any, ...], Composition] = OrderedDict()
        self._composing = threading.Lock()
        self._held: Refreshed[Composition] = Refreshed(
            read=self._read,
            build=self._logged,
            what="the roles defined at runtime",
            unread=lambda: Composition(catalog=self.code),
            check_every=check_every,
            clock=clock,
            kept=tenants_kept,
        )

    @property
    def permissions(self) -> frozenset[str]:
        """What a defined role may grant, now."""
        given = self._permissions
        return frozenset(given if isinstance(given, Iterable) else given())

    def scopes(self, tenant: str | None) -> tuple[DefinedRoles, DefinedRoles]:
        """What every tenant shares and what `tenant` defines — read now, nothing for `None`."""
        shared = self.definitions.read(None)
        own = DefinedRoles() if tenant is None else self.definitions.read(tenant)
        return shared, own

    def composed(
        self, tenant: str | None, shared: DefinedRoles, own: DefinedRoles
    ) -> Composition:
        """The composition of `tenant`'s documents as stored — composed once per fingerprint,
        then kept. A document not stored yet is `compose`d, never kept here."""
        key = (tenant, *fingerprint(shared, own))
        with self._composing:
            found = self._composed.get(key)
        if found is not None:
            return found
        composition = compose(self.code, shared, own, tenant)
        with self._composing:
            self._composed[key] = composition
            while len(self._composed) > self.tenants_kept:
                self._composed.popitem(last=False)
        return composition

    def _read(self, tenant: str | None) -> tuple[tuple[Hashable, ...], Any]:
        """The fingerprint of `tenant`'s scopes, and the tenant with its documents."""
        shared, own = self.scopes(tenant)
        return fingerprint(shared, own), (tenant, shared, own)

    def _logged(self, content: tuple[str | None, DefinedRoles, DefinedRoles]) -> Composition:
        """The composition of what was read, each role left out logged."""
        composition = self.composed(*content)
        for key, why in composition.problems.items():
            logger.error(f"the role {key} defined at runtime grants nothing: {why}")
        return composition

    def in_effect(self, tenant: str | None = None) -> Composition:
        """The composition in effect for `tenant` — what every tenant shares for `None`."""
        return self._held.get(tenant)

    def current(self, tenant: str | None = None) -> GrantCatalog:
        """The catalog in effect for `tenant` — what every tenant shares for `None`."""
        return self.in_effect(tenant).catalog

    def latest(self, tenant: str | None = None) -> Composition:
        """`tenant`'s catalog as stored now, past the wait of `check_every`."""
        return self.composed(tenant, *self.scopes(tenant))

    def problems(self, tenant: str | None = None) -> dict[str, str]:
        """The roles defined for `tenant` that grant nothing, by catalog key, and why — what a
        screen shows to be fixed."""
        return dict(self._held.get(tenant).problems)

    def defined(self, tenant: str | None = None) -> tuple[RoleDefinition, ...]:
        """The roles defined for exactly this scope — what a screen lists as editable."""
        return self.definitions.read(tenant).roles

    def stale(self, tenant: str | None = None) -> None:
        """Read `tenant`'s scopes again on the next call — every tenant's for `None`."""
        self._held.stale(tenant, everything=tenant is None)
