"""Where roles defined at runtime are stored: on a `KeyValueStore` — the running context's when
none is given (Redis), or `DatabaseKeyValue` on the project's own database — or in this process
(`InMemoryRoleDefinitions`, the same over an `InMemoryKeyValue`); one versioned document per
scope, written by compare-and-swap.

    KeyValueRoleDefinitions()                         the context's store
    KeyValueRoleDefinitions(DatabaseKeyValue(...))    a table the project migrates
"""

from sincpro_framework.auth.common.adapters.documents import KeyValueDocuments
from sincpro_framework.auth.grants.domain.definitions import DefinedRoles, RoleDefinitions
from sincpro_framework.data_layer.store import InMemoryKeyValue, KeyValueStore


class KeyValueRoleDefinitions(KeyValueDocuments[DefinedRoles], RoleDefinitions):
    """The roles every replica shares — `<key>` for every tenant, `<key>:<tenant>` for one."""

    def __init__(
        self, store: KeyValueStore | None = None, key: str = "sincpro:auth:roles"
    ) -> None:
        """`store` — the running context's when not given."""
        super().__init__(DefinedRoles, key, store)


class InMemoryRoleDefinitions(KeyValueRoleDefinitions):
    """The roles of this process — a test, a single replica."""

    def __init__(self) -> None:
        super().__init__(InMemoryKeyValue())
