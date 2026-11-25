"""`TenantCatalogs`: the catalog in effect for each tenant — the roles the code declares, the ones
defined at runtime for every tenant, and the tenant's own — what a resolver reads on every call.

    catalogs = TenantCatalogs(catalog, KeyValueRoleDefinitions(), permissions=BillingPermission)
    GrantResolver(catalogs, grants)                     # each identity in its tenant's catalog
    RoleAdministration(catalogs, grants, ...)           # what changes it

Context: it only reads. A replica reads a tenant's versions at most once each `check_interval` (a
second), one caller per tenant, never holding back another tenant, and composes again only when
a version moved. `load(tenant)` reads now, past that wait — what an administration checks a
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

from sincpro_framework.auth.common.domain.documents import fingerprint
from sincpro_framework.auth.common.infrastructure.scoped_cache import ScopedCache
from sincpro_framework.auth.grants.domain.catalog import EffectiveCatalog, GrantCatalog
from sincpro_framework.auth.grants.domain.catalog_builder import CatalogBuilder
from sincpro_framework.auth.grants.domain.definitions import (
    DefinedRoles,
    RoleDefinition,
    RoleDefinitions,
)
from sincpro_framework.sincpro_logger import logger


class TenantCatalogs:
    """The code's catalog with the roles `definitions` stores, composed per tenant."""

    def __init__(
        self,
        catalog: GrantCatalog,
        definitions: RoleDefinitions,
        permissions: Iterable[str] | Callable[[], Iterable[str]] | None = None,
        check_interval: timedelta = timedelta(seconds=1),
        clock: Callable[[], float] = time.monotonic,
        max_tenants: int = 4096,
    ) -> None:
        """`permissions` is what a defined role may grant — every value of the project's
        `Permission` classes, or what answers them when asked; what the code's roles grant when
        not given. `max_tenants` bounds how many tenants' catalogs a replica caches at once.
        """
        self.code = catalog
        self.builder = CatalogBuilder(catalog)
        self.definitions = definitions
        self._permissions = (
            permissions
            if permissions is not None
            else frozenset().union(*(role.grants for role in catalog.roles.values()))
        )
        self.max_tenants = max_tenants
        self._composed: OrderedDict[tuple[Any, ...], EffectiveCatalog] = OrderedDict()
        self._composing = threading.Lock()
        self._refreshed: ScopedCache[EffectiveCatalog] = ScopedCache(
            read=self._read,
            build=self._compose_logged,
            what="the roles defined at runtime",
            fallback=lambda: EffectiveCatalog(catalog=self.code),
            check_interval=check_interval,
            clock=clock,
            max_entries=max_tenants,
        )

    @property
    def permissions(self) -> frozenset[str]:
        """What a defined role may grant, now."""
        given = self._permissions
        return frozenset(given if isinstance(given, Iterable) else given())

    def load_scopes(self, tenant: str | None) -> tuple[DefinedRoles, DefinedRoles]:
        """What every tenant shares and what `tenant` defines — read now, nothing for `None`."""
        shared = self.definitions.read(None)
        own = DefinedRoles() if tenant is None else self.definitions.read(tenant)
        return shared, own

    def compose(
        self, tenant: str | None, shared: DefinedRoles, own: DefinedRoles
    ) -> EffectiveCatalog:
        """The catalog of `tenant`'s documents as stored — composed once per fingerprint, then
        cached, the oldest evicted past `max_tenants`. A document not stored yet is composed
        by `CatalogBuilder.compose`, never cached here."""
        key = (tenant, *fingerprint(shared, own))
        with self._composing:
            found = self._composed.get(key)
        if found is not None:
            return found
        effective = self.builder.compose(shared, own, tenant)
        with self._composing:
            self._composed[key] = effective
            while len(self._composed) > self.max_tenants:
                self._composed.popitem(last=False)
        return effective

    def _read(self, tenant: str | None) -> tuple[tuple[Hashable, ...], Any]:
        """The fingerprint of `tenant`'s scopes, and the tenant with its documents."""
        shared, own = self.load_scopes(tenant)
        return fingerprint(shared, own), (tenant, shared, own)

    def _compose_logged(
        self, content: tuple[str | None, DefinedRoles, DefinedRoles]
    ) -> EffectiveCatalog:
        """The catalog of what was read, each role left out logged."""
        effective = self.compose(*content)
        for key, why in effective.problems.items():
            logger.error(f"the role {key} defined at runtime grants nothing: {why}")
        return effective

    def cached(self, tenant: str | None = None) -> EffectiveCatalog:
        """The catalog in effect for `tenant` — what every tenant shares for `None` — as this
        replica holds it, read again at most each `check_interval`."""
        return self._refreshed.get(tenant)

    def catalog(self, tenant: str | None = None) -> GrantCatalog:
        """The `GrantCatalog` in effect for `tenant` — what every tenant shares for `None`."""
        return self.cached(tenant).catalog

    def load(self, tenant: str | None = None) -> EffectiveCatalog:
        """`tenant`'s catalog as stored now, past the wait of `check_interval`."""
        return self.compose(tenant, *self.load_scopes(tenant))

    def problems(self, tenant: str | None = None) -> dict[str, str]:
        """The roles defined for `tenant` that grant nothing, by catalog key, and why — what a
        screen shows to be fixed."""
        return dict(self.cached(tenant).problems)

    def defined(self, tenant: str | None = None) -> tuple[RoleDefinition, ...]:
        """The roles defined for exactly this scope — what a screen lists as editable."""
        return self.definitions.read(tenant).roles

    def invalidate(self, tenant: str | None = None) -> None:
        """Read `tenant`'s scopes again on the next call — every tenant's for `None`."""
        self._refreshed.invalidate(tenant, everything=tenant is None)
