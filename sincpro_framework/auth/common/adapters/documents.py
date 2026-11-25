"""Where versioned documents per scope are stored: in memory, or on a `KeyValueStore` — the
running context's when none is given (Redis), or `DatabaseKeyValue` on the project's own database
— one key per scope: `<key>` for what every tenant shares, `<key>:<tenant>` for one.

    roles = KeyValueDocuments(DefinedRoles, "sincpro:auth:roles")
    roles.read("bo")              ->  DefinedRoles(version=3, roles=(...))
    roles.write("bo", updated)    ->  False when another administrator wrote version 4 first

Context: the first version is written with `add` and every one after with `replace` —
compare-and-swap — so of two writers over one version exactly one is stored; the other reads
again (`update_document`).
"""

import threading
from collections.abc import Callable

from sincpro_framework.auth.common.adapters.key_value import KeyValueStoreMixin
from sincpro_framework.auth.common.domain.documents import ScopedDocuments, Versioned
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


class KeyValueDocuments[D: DataTransferObject](KeyValueStoreMixin, ScopedDocuments[D]):
    """The documents every replica shares, one JSON document per scope — compare-and-swap."""

    warn_when_unshared = True
    """A document in this process alone is what one replica changed and no other reads."""

    def __init__(self, model: type[D], key: str, store: KeyValueStore | None = None) -> None:
        """`model` — the document's type, a DTO with a `version` field; `key` — the prefix of
        every scope's key; `store` — the running context's when not given."""
        self._init_key_value_store(store, key)
        self.model = model

    def _key(self, scope: str | None) -> str:
        """The scope's key: `<key>` for what every tenant shares, `<key>:<tenant>`."""
        return self.prefix if scope is None else f"{self.prefix}:{scope}"

    def read(self, scope: str | None = None) -> D:
        """The scope's document — `model()`, version 0, while none was written."""
        raw = self.store().get(self._key(scope))
        return self.model() if raw is None else self.model.model_validate_json(raw)

    def write(self, scope: str | None, document: D) -> bool:
        """`document`, only when the version stored now is the one before it — added when it is
        the first, replaced by compare-and-swap after."""
        store = self.store()
        key = self._key(scope)
        value = document.model_dump_json().encode()
        expected = int(getattr(document, "version")) - 1
        if expected == 0:
            return store.add(key, value)
        current = store.get(key)
        if current is None or (
            int(getattr(self.model.model_validate_json(current), "version", 0)) != expected
        ):
            return False
        return store.replace(key, current, value)
