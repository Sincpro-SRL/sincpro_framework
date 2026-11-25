"""One versioned document on a `KeyValueStore`, written only over the version it was read at —
how what an administrator changes at runtime is kept: roles defined, requirements added.

    roles = KeyValueDocument(DefinedRoles, "sincpro:auth:roles")
    roles.read()                          ->  DefinedRoles(version=3, roles=(...))
    roles.write(read.version, updated)    ->  False when another administrator wrote first

Context: `add` writes the first version and `replace` — compare-and-swap — every one after, so of
two writers over one version exactly one is kept; the other reads again. The store is the one
given, else the running context's (Redis), or `DatabaseKeyValue` on the project's database.
"""

from sincpro_framework.context.entrypoint.resolution import current_store, key_value_store_of
from sincpro_framework.data_layer.store import KeyValueStore
from sincpro_framework.sincpro_abstractions import DataTransferObject


def version_of(document: DataTransferObject) -> int:
    """The `version` a document says — 0 for one that says none."""
    return int(getattr(document, "version", 0))


class KeyValueDocument[D: DataTransferObject]:
    """The document of type `model` — a DTO with a `version` field — under `key`."""

    def __init__(self, model: type[D], key: str, store: KeyValueStore | None = None) -> None:
        self.model = model
        self.key = key
        self._store = store

    def store(self) -> KeyValueStore:
        """The store given, else the running context's."""
        return self._store if self._store is not None else key_value_store_of(current_store())

    def read(self) -> D:
        """The document kept — `model()`, version 0, while none was written."""
        raw = self.store().get(self.key)
        return self.model() if raw is None else self.model.model_validate_json(raw)

    def write(self, expected_version: int, document: D) -> bool:
        """Keep `document` — only when the version kept now is `expected_version`."""
        store = self.store()
        value = document.model_dump_json().encode()
        if expected_version == 0:
            return store.add(self.key, value)
        current = store.get(self.key)
        if current is None or version_of(self.model.model_validate_json(current)) != (
            expected_version
        ):
            return False
        return store.replace(self.key, current, value)
