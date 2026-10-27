"""`InMemoryObjectStore`: objects held in this process — tests, and a service with one replica."""

import threading
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from typing import Any

from sincpro_framework.data_layer.object_storage.domain.object_store import (
    ObjectRef,
    ObjectStore,
)


class InMemoryObjectStore(ObjectStore):
    """Objects in a dict, under a lock."""

    def __init__(self) -> None:
        """Empty."""
        self._held: dict[str, tuple[bytes, ObjectRef]] = {}
        self._lock = threading.Lock()

    def put(
        self,
        key: str,
        data: bytes,
        content_type: str,
        metadata: Mapping[str, str],
        if_absent: bool,
        **options: Any,
    ) -> bool:
        stored = ObjectRef(
            key=key,
            size=len(data),
            content_type=content_type,
            metadata=dict(metadata),
            modified_at=datetime.now(UTC),
        )
        with self._lock:
            if if_absent and key in self._held:
                return False
            self._held[key] = (bytes(data), stored)
        return True

    def get(self, key: str) -> bytes | None:
        with self._lock:
            found = self._held.get(key)
        return None if found is None else found[0]

    def info(self, key: str) -> ObjectRef | None:
        with self._lock:
            found = self._held.get(key)
        return None if found is None else found[1]

    def delete(self, key: str) -> None:
        with self._lock:
            self._held.pop(key, None)

    def keys(self, prefix: str) -> Iterator[str]:
        with self._lock:
            held = sorted(self._held)
        return (key for key in held if key.startswith(prefix))
