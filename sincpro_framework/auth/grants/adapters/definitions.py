"""Where roles defined at runtime are kept: in memory, or on a `KeyValueStore` — the running
context's when none is given (Redis), or `DatabaseKeyValue` on the project's own database.

    KeyValueRoleDefinitions()                         the context's store
    KeyValueRoleDefinitions(DatabaseKeyValue(...))    a table the project migrates

Context: the document is one key, written with `add` the first time and `replace` after —
compare-and-swap, so of two administrators writing over one version exactly one is kept.
"""

import threading
from collections.abc import Sequence

from sincpro_framework.auth.grants.domain.definitions import (
    DefinedRoles,
    RoleDefinition,
    RoleDefinitions,
)
from sincpro_framework.context.entrypoint.resolution import current_store, key_value_store_of
from sincpro_framework.data_layer.store import KeyValueStore


class InMemoryRoleDefinitions(RoleDefinitions):
    """The roles of this process — a test, a single replica."""

    def __init__(self) -> None:
        self._defined = DefinedRoles()
        self._lock = threading.Lock()

    def read(self) -> DefinedRoles:
        """What is defined now."""
        return self._defined

    def write(self, expected_version: int, roles: Sequence[RoleDefinition]) -> bool:
        """The next version — when nobody wrote since `expected_version`."""
        with self._lock:
            if self._defined.version != expected_version:
                return False
            self._defined = DefinedRoles(version=expected_version + 1, roles=tuple(roles))
            return True


class KeyValueRoleDefinitions(RoleDefinitions):
    """The roles every replica shares, as one JSON document under `key`."""

    def __init__(
        self, store: KeyValueStore | None = None, key: str = "sincpro:auth:roles"
    ) -> None:
        """`store` — the running context's when not given."""
        self._store = store
        self.key = key

    def store(self) -> KeyValueStore:
        """The store given, else the running context's."""
        return self._store if self._store is not None else key_value_store_of(current_store())

    def read(self) -> DefinedRoles:
        """The document kept — `DefinedRoles()` while none was written."""
        raw = self.store().get(self.key)
        return DefinedRoles() if raw is None else DefinedRoles.model_validate_json(raw)

    def write(self, expected_version: int, roles: Sequence[RoleDefinition]) -> bool:
        """`add` over nothing for the first version; `replace` over the bytes read after."""
        store = self.store()
        written = DefinedRoles(version=expected_version + 1, roles=tuple(roles))
        value = written.model_dump_json().encode()
        if expected_version == 0:
            return store.add(self.key, value)
        current = store.get(self.key)
        if (
            current is None
            or DefinedRoles.model_validate_json(current).version != expected_version
        ):
            return False
        return store.replace(self.key, current, value)
