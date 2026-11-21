"""Where versioned documents per scope are kept: in memory, or on a `KeyValueStore` — the running
context's when none is given (Redis), or `DatabaseKeyValue` on the project's own database — one
key per scope: `<key>` for what every tenant shares, `<key>:<tenant>` for one."""

import threading
from collections.abc import Callable

from sincpro_framework.auth.domain.documents import ScopedDocuments, Versioned
from sincpro_framework.auth.infrastructure.documents import KeyValueDocument
from sincpro_framework.data_layer.store import KeyValueStore
from sincpro_framework.sincpro_abstractions import DataTransferObject


class InMemoryDocuments[D: Versioned](ScopedDocuments[D]):
    """The documents of this process — a test, a single replica."""

    def __init__(self, empty: Callable[[], D]) -> None:
        """`empty` — the document of a scope never written."""
        self.empty = empty
        self._documents: dict[str | None, D] = {}
        self._lock = threading.Lock()

    def read(self, scope: str | None = None) -> D:
        """What the scope holds now."""
        return self._documents.get(scope) or self.empty()

    def write(self, scope: str | None, document: D) -> bool:
        """`document` — when nobody wrote the scope since the version before it."""
        with self._lock:
            if self.read(scope).version != document.version - 1:
                return False
            self._documents[scope] = document
            return True


class KeyValueDocuments[D: DataTransferObject](ScopedDocuments[D]):
    """The documents every replica shares, one JSON document per scope — compare-and-swap."""

    def __init__(self, model: type[D], key: str, store: KeyValueStore | None = None) -> None:
        """`model` — the document's type; `store` — the running context's when not given."""
        self.model = model
        self.key = key
        self._store = store

    def _document(self, scope: str | None) -> KeyValueDocument[D]:
        """The scope's document: `<key>` for what every tenant shares, `<key>:<tenant>`."""
        scoped = self.key if scope is None else f"{self.key}:{scope}"
        return KeyValueDocument(self.model, scoped, self._store)

    def read(self, scope: str | None = None) -> D:
        """The scope's document — `model()` while none was written."""
        return self._document(scope).read()

    def write(self, scope: str | None, document: D) -> bool:
        """`document`, over the version before it."""
        return self._document(scope).write(getattr(document, "version") - 1, document)
