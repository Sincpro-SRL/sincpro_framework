"""Where roles defined at runtime are kept: in memory, or on a `KeyValueStore` — the running
context's when none is given (Redis), or `DatabaseKeyValue` on the project's own database.

    KeyValueRoleDefinitions()                         the context's store
    KeyValueRoleDefinitions(DatabaseKeyValue(...))    a table the project migrates

Context: each scope is one key — `<key>` for every tenant, `<key>:<tenant>` for one — a
`KeyValueDocument`: compare-and-swap, so of two administrators writing over one version exactly
one is kept.
"""

import threading

from sincpro_framework.auth.grants.domain.definitions import DefinedRoles, RoleDefinitions
from sincpro_framework.auth.infrastructure.documents import KeyValueDocument
from sincpro_framework.data_layer.store import KeyValueStore


class InMemoryRoleDefinitions(RoleDefinitions):
    """The roles of this process — a test, a single replica."""

    def __init__(self) -> None:
        self._defined: dict[str | None, DefinedRoles] = {}
        self._lock = threading.Lock()

    def read(self, tenant: str | None = None) -> DefinedRoles:
        """What the scope defines now."""
        return self._defined.get(tenant, DefinedRoles())

    def write(self, tenant: str | None, document: DefinedRoles) -> bool:
        """`document` — when nobody wrote the scope since the version before it."""
        with self._lock:
            if self.read(tenant).version != document.version - 1:
                return False
            self._defined[tenant] = document
            return True


class KeyValueRoleDefinitions(RoleDefinitions):
    """The roles every replica shares, one JSON document per scope."""

    def __init__(
        self, store: KeyValueStore | None = None, key: str = "sincpro:auth:roles"
    ) -> None:
        """`store` — the running context's when not given."""
        self._store = store
        self.key = key

    def _document(self, tenant: str | None) -> KeyValueDocument[DefinedRoles]:
        """The scope's document: `<key>` for every tenant, `<key>:<tenant>` for one."""
        scoped = self.key if tenant is None else f"{self.key}:{tenant}"
        return KeyValueDocument(DefinedRoles, scoped, self._store)

    def read(self, tenant: str | None = None) -> DefinedRoles:
        """The scope's document — `DefinedRoles()` while none was written."""
        return self._document(tenant).read()

    def write(self, tenant: str | None, document: DefinedRoles) -> bool:
        """`document`, over the version before it — compare-and-swap."""
        return self._document(tenant).write(document.version - 1, document)
