"""Where roles defined at runtime are kept: in memory, or on a `KeyValueStore` — the running
context's when none is given (Redis), or `DatabaseKeyValue` on the project's own database — one
versioned document per scope, written by compare-and-swap.

    KeyValueRoleDefinitions()                         the context's store
    KeyValueRoleDefinitions(DatabaseKeyValue(...))    a table the project migrates
"""

from sincpro_framework.auth.common.adapters.documents import (
    InMemoryDocuments,
    KeyValueDocuments,
)
from sincpro_framework.auth.grants.domain.definitions import DefinedRoles, RoleDefinitions
from sincpro_framework.data_layer.store import KeyValueStore


class InMemoryRoleDefinitions(InMemoryDocuments[DefinedRoles], RoleDefinitions):
    """The roles of this process — a test, a single replica."""

    def __init__(self) -> None:
        super().__init__(DefinedRoles)


class KeyValueRoleDefinitions(KeyValueDocuments[DefinedRoles], RoleDefinitions):
    """The roles every replica shares — `<key>` for every tenant, `<key>:<tenant>` for one."""

    def __init__(
        self, store: KeyValueStore | None = None, key: str = "sincpro:auth:roles"
    ) -> None:
        """`store` — the running context's when not given."""
        super().__init__(DefinedRoles, key, store)
