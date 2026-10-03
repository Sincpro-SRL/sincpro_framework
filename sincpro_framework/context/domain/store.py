"""`ContextStore`: contexts kept by name outside the process — a flow to resume, a session, a
tenant's defaults, the process level every replica reads.

    store.keep("tenant:acme", {"lang": "es", "plan": "pro"})
    store.keep(f"session:{sid}", {"user_id": "ana"}, ttl=timedelta(hours=8))
    store.restore("tenant:acme")   →  {"lang": "es", "plan": "pro"}       None when gone
    store.version("tenant:acme")   →  2                                    0 when never kept

**Not a cache.** A cache keeps what can be computed again; this keeps what a flow *is*, written by
the flow and read by whoever continues it. Opt-in: by default a context travels with its message.

Implemented in memory (`InMemoryContexts`), over any `KeyValueStore` — Redis, Valkey, Memcached —
(`KeyValueContexts`), or by a project over its own storage.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from datetime import timedelta
from typing import Any


class ContextStore(ABC):
    @abstractmethod
    def keep(self, key: str, values: Mapping[str, Any], ttl: timedelta | None = None) -> None:
        """`values` under `key`, replacing what was there, until `ttl` passes — forever without
        one. Each keep moves the key's version."""

    @abstractmethod
    def restore(self, key: str) -> dict[str, Any] | None:
        """What `key` holds, or `None` when nothing was kept or its time passed."""

    @abstractmethod
    def forget(self, key: str) -> None:
        """`key` is gone."""

    @abstractmethod
    def version(self, key: str) -> int:
        """How many times `key` was kept — what a reader compares to know it changed."""
