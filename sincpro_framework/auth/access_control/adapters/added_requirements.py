"""Where the requirements added at runtime are stored: in memory, or on a `KeyValueStore` — the
running context's when none is given (Redis), or `DatabaseKeyValue` on the project's database —
one versioned document per scope, written by compare-and-swap."""

from sincpro_framework.auth.access_control.domain.added_requirements import (
    AddedRequirements,
    AddedRequirementStore,
)
from sincpro_framework.auth.common.adapters.documents import (
    InMemoryDocuments,
    KeyValueDocuments,
)
from sincpro_framework.data_layer.store import KeyValueStore


class InMemoryAddedRequirements(InMemoryDocuments[AddedRequirements], AddedRequirementStore):
    """The requirements of this process — a test, a single replica."""

    def __init__(self) -> None:
        super().__init__(AddedRequirements)


class KeyValueAddedRequirements(KeyValueDocuments[AddedRequirements], AddedRequirementStore):
    """The requirements every replica shares — `<key>` for every tenant, `<key>:<tenant>`."""

    def __init__(
        self, store: KeyValueStore | None = None, key: str = "sincpro:auth:requirements"
    ) -> None:
        """`store` — the running context's when not given."""
        super().__init__(AddedRequirements, key, store)
