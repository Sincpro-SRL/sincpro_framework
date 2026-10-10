"""The context stores the framework ships.

    InMemoryContexts()                                  one process: tests, a single worker
    KeyValueContexts(RedisKeyValue(redis))              every replica and service sharing Redis
    KeyValueContexts(store, TypedCodec(SIATContext))    the types back, the same codebase

`KeyValueContexts` stands on `KeyValueStore`, so Redis, Valkey and Memcached are the adapters
`sincpro_framework.data_layer.caching` already has — behind their extras, nothing new to install. A key is
written in one `SET`; its version is an atomic `INCR` beside it.
"""

import threading
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from sincpro_framework.common.store import KeyValueStore
from sincpro_framework.context.adapters.codecs import PlainCodec
from sincpro_framework.context.domain.codec import ContextCodec
from sincpro_framework.context.domain.store import ContextStore


class InMemoryContexts(ContextStore):
    def __init__(self) -> None:
        self._kept: dict[str, tuple[dict[str, Any], datetime | None]] = {}
        self._versions: dict[str, int] = {}
        self._lock = threading.Lock()

    def keep(self, key: str, values: Mapping[str, Any], ttl: timedelta | None = None) -> None:
        until = None if ttl is None else datetime.now(UTC) + ttl
        with self._lock:
            self._kept[key] = (dict(values), until)
            self._versions[key] = self._versions.get(key, 0) + 1

    def restore(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            found = self._kept.get(key)
            if found is None:
                return None
            values, until = found
            if until is not None and until <= datetime.now(UTC):
                del self._kept[key]
                return None
            return dict(values)

    def forget(self, key: str) -> None:
        with self._lock:
            self._kept.pop(key, None)

    def version(self, key: str) -> int:
        with self._lock:
            return self._versions.get(key, 0)


class KeyValueContexts(ContextStore):
    def __init__(
        self,
        store: KeyValueStore,
        codec: ContextCodec | None = None,
        prefix: str = "sincpro:context",
    ) -> None:
        """`codec` is how a context is written — plain JSON by default, readable by any language."""
        self.store = store
        self.codec = codec or PlainCodec()
        self.prefix = prefix

    def _key(self, key: str) -> str:
        return f"{self.prefix}:{key}"

    def _version_key(self, key: str) -> str:
        return f"{self._key(key)}:version"

    def keep(self, key: str, values: Mapping[str, Any], ttl: timedelta | None = None) -> None:
        """`values` under `key`, its version moved — both gone after `ttl`, so a store that keeps
        a node per execution never grows without end."""
        self.store.set(self._key(key), self.codec.dumps(values), ttl)
        moved = self.store.increment(self._version_key(key))
        if ttl is not None:
            self.store.set(self._version_key(key), str(moved).encode(), ttl)

    def restore(self, key: str) -> dict[str, Any] | None:
        data = self.store.get(self._key(key))
        return None if data is None else self.codec.loads(data)

    def forget(self, key: str) -> None:
        self.store.delete(self._key(key))
        self.store.delete(self._version_key(key))

    def version(self, key: str) -> int:
        counted = self.store.get(self._version_key(key))
        return 0 if counted is None else int(counted)
