"""Where the requirements added at runtime are kept: in memory, or on a `KeyValueStore` — the
running context's when none is given (Redis), or `DatabaseKeyValue` on the project's database —
one document per scope: `<key>` for every tenant, `<key>:<tenant>` for one."""

import threading

from sincpro_framework.auth.domain.added_requirements import (
    AddedRequirements,
    AddedRequirementStore,
)
from sincpro_framework.auth.infrastructure.documents import KeyValueDocument
from sincpro_framework.data_layer.store import KeyValueStore


class InMemoryAddedRequirements(AddedRequirementStore):
    """The requirements of this process — a test, a single replica."""

    def __init__(self) -> None:
        self._added: dict[str | None, AddedRequirements] = {}
        self._lock = threading.Lock()

    def read(self, tenant: str | None = None) -> AddedRequirements:
        """What the scope requires now."""
        return self._added.get(tenant, AddedRequirements())

    def write(self, tenant: str | None, document: AddedRequirements) -> bool:
        """`document` — when nobody wrote the scope since the version before it."""
        with self._lock:
            if self.read(tenant).version != document.version - 1:
                return False
            self._added[tenant] = document
            return True


class KeyValueAddedRequirements(AddedRequirementStore):
    """The requirements every replica shares, one JSON document per scope."""

    def __init__(
        self, store: KeyValueStore | None = None, key: str = "sincpro:auth:requirements"
    ) -> None:
        """`store` — the running context's when not given."""
        self._store = store
        self.key = key

    def _document(self, tenant: str | None) -> KeyValueDocument[AddedRequirements]:
        """The scope's document."""
        scoped = self.key if tenant is None else f"{self.key}:{tenant}"
        return KeyValueDocument(AddedRequirements, scoped, self._store)

    def read(self, tenant: str | None = None) -> AddedRequirements:
        """The scope's document — `AddedRequirements()` while none was written."""
        return self._document(tenant).read()

    def write(self, tenant: str | None, document: AddedRequirements) -> bool:
        """`document`, over the version before it — compare-and-swap."""
        return self._document(tenant).write(document.version - 1, document)
