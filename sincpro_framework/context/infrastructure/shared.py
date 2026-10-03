"""The process level shared by every replica, through a store.

    use_context().root.share(KeyValueContexts(RedisKeyValue(redis)), every=timedelta(seconds=5))
    use_context().root.set("maintenance", True)       every replica reads it within `every`

**Read locally, refreshed by version.** A read is the local mapping; at most once per `every` the
store's version is asked, and the values are read again only when it moved — never a network call per
read. A write is local at once and kept in the store, moving its version for the others.

A value under a key that is not text — a store, a settings object — stays in this process: a store
keeps what travels.
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
        super().__init__()
        self._store = store
        self._key = key
        self._every = every.total_seconds()
        self._checked = 0.0
        self._version = -1
        self._current = MappingProxyType(dict(seed or {}))
        if store.version(key) == 0 and seed:
            self._kept()
        self._refreshed()

    @property
    def current(self) -> Mapping[Any, Any]:
        if time.monotonic() - self._checked >= self._every:
            self._refreshed()
        return self._current

    def _refreshed(self) -> None:
        self._checked = time.monotonic()
        version = self._store.version(self._key)
        if version == self._version:
            return
        kept = self._store.restore(self._key) or {}
        local_only = {
            key: value for key, value in self._current.items() if not isinstance(key, str)
        }
        self._current = MappingProxyType({**local_only, **kept})
        self._version = version

    def _kept(self) -> None:
        named = {key: value for key, value in self._current.items() if isinstance(key, str)}
        self._store.keep(self._key, named)
        self._version = self._store.version(self._key)

    def set(self, key: Any, value: Any) -> None:
        super().set(key, value)
        self._kept()

    def update(self, values: Mapping[Any, Any]) -> None:
        super().update(values)
        self._kept()

    def unset(self, key: Any) -> None:
        super().unset(key)
        self._kept()

    def replace(self, values: Mapping[Any, Any]) -> None:
        super().replace(values)
        self._kept()
