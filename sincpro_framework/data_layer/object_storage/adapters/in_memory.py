"""`InMemoryObjectStore`: objects held in this process — tests, and a service with one replica. What
expired is dropped on every write and listing, so nothing unclaimed stays in memory for good.
"""

import threading
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from typing import Any, BinaryIO

from sincpro_framework.data_layer.object_storage.domain.object_store import (
    ObjectRef,
    ObjectStore,
    StoredObject,
)


class InMemoryObjectStore(ObjectStore):
    """Objects in a dict, under a lock."""

    def __init__(self) -> None:
        """Empty."""
        self._held: dict[str, StoredObject] = {}
        self._lock = threading.Lock()

    def _drop_expired(self) -> None:
        """Forget every object past its `expires_at` — called under the lock."""
        moment = datetime.now(UTC)
        for key, held in list(self._held.items()):
            if held.ref.expires_at is not None and held.ref.expires_at <= moment:
                del self._held[key]

    def put(
        self,
        key: str,
        data: bytes | BinaryIO,
        content_type: str,
        metadata: Mapping[str, str],
        if_absent: bool,
        expires_at: datetime | None,
        **options: Any,
    ) -> bool:
        data = data if isinstance(data, bytes) else data.read()
        stored = ObjectRef(
            key=key,
            size=len(data),
            content_type=content_type,
            metadata=dict(metadata),
            modified_at=datetime.now(UTC),
            expires_at=expires_at,
        )
        with self._lock:
            self._drop_expired()
            if if_absent and key in self._held:
                return False
            self._held[key] = StoredObject(data=data, ref=stored)
        return True

    def get(self, key: str, **options: Any) -> StoredObject | None:
        with self._lock:
            return self._held.get(key)

    def info(self, key: str, **options: Any) -> ObjectRef | None:
        with self._lock:
            found = self._held.get(key)
        return None if found is None else found.ref

    def delete(self, key: str, **options: Any) -> None:
        with self._lock:
            self._held.pop(key, None)

    def keys(self, prefix: str, **options: Any) -> Iterator[str]:
        with self._lock:
            self._drop_expired()
            held = sorted(self._held)
        return (key for key in held if key.startswith(prefix))
