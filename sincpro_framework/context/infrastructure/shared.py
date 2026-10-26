"""The process level shared by every replica, through a store.

    use_context().root.share(KeyValueContexts(RedisKeyValue(redis)), every=timedelta(seconds=5))
    use_context().root.set("maintenance", True)       every replica reads it within `every`

**Read locally, refreshed by version.** A read is the local mapping; at most once per `every` the
store's version is asked, and the values are read again only when it moved — never a network call per
read. A write is local at once and saved in the store, moving its version for the others.

A value under a key that is not text — a store, a settings object — stays in this process: a store
saves what travels.
"""

import time
from collections.abc import Mapping
from datetime import timedelta
from types import MappingProxyType
from typing import Any

from sincpro_framework.context.domain.node import Values
from sincpro_framework.context.domain.store import ContextStore


class SharedValues(Values):
    """`Values` a store shares across processes."""

    __slots__ = ("_store", "_key", "_every", "_checked", "_version")

    def __init__(
        self,
        store: ContextStore,
        key: str,
        every: timedelta,
        seed: Mapping[Any, Any] | None = None,
    ) -> None:
        """Values under `key` in `store`, re-read at most once per `every`.

        1. Start from `seed` — what the process held before it joined the store.
        2. A key the store never saved takes the seed: the first replica brings its defaults.
        Final: read what the store holds now — a key saved by another replica wins over the
        seed.

            SharedValues(store, "process:billing", timedelta(seconds=5), seed={"lang": "es"})
                ->  first replica: {"lang": "es"}, saved as "process:billing"
                ->  a later one, the store holding {"lang": "en"}: {"lang": "en"}

        Nothing is refreshed after this until `every` has passed since the last check.
        """
        super().__init__()
        self._store = store
        self._key = key
        self._every = every.total_seconds()
        self._checked = 0.0
        self._version = -1
        self._current = MappingProxyType(dict(seed or {}))
        if store.version(key) == 0 and seed:
            self._saved()
        self._refreshed()

    def _refreshed(self) -> None:
        """Ask the store's version and read the values again only when it moved.

        1. Note the time of this check.
        2. The same version as last read: nothing to read.
        3. Otherwise the store's values, with what lives only here (keys that are not text)
           left in place.
        Final: the version read remembered.
        """
        self._checked = time.monotonic()
        version = self._store.version(self._key)
        if version == self._version:
            return
        stored = self._store.load(self._key) or {}
        local_only = {
            key: value for key, value in self._current.items() if not isinstance(key, str)
        }
        self._current = MappingProxyType({**local_only, **stored})
        self._version = version

    @property
    def current(self) -> Mapping[Any, Any]:
        """The local values — refreshed first when `every` passed since the last check, so a
        read costs a store round trip at most once per `every`."""
        if time.monotonic() - self._checked >= self._every:
            self._refreshed()
        return self._current

    def _saved(self) -> None:
        """Save the text-keyed values under the key, and remember the version that write made
        — so this replica does not read back its own write."""
        self._store.save(self._key, self.named())
        self._version = self._store.version(self._key)

    def set(self, key: Any, value: Any) -> None:
        """Written locally at once, then saved for the other replicas."""
        super().set(key, value)
        self._saved()

    def update(self, values: Mapping[Any, Any]) -> None:
        """Written locally at once, then saved for the other replicas."""
        super().update(values)
        self._saved()

    def unset(self, key: Any) -> None:
        """Taken away locally at once, then saved for the other replicas."""
        super().unset(key)
        self._saved()

    def replace(self, values: Mapping[Any, Any]) -> None:
        """Replaced locally at once, then saved for the other replicas."""
        super().replace(values)
        self._saved()
