"""`Objects`: storing and reading objects by key — the rules over the port: a missing key is
`ObjectNotFound`, a taken one under `if_absent` is `ObjectAlreadyExists`, a content type not given
is guessed from the key.

    objects = Objects(store)
    objects.put("exports/invoices.csv", data)          ->  ObjectRef(content_type="text/csv", …)
    objects.get("never")                               ->  ObjectNotFound
"""

import mimetypes
from collections.abc import Iterator, Mapping
from typing import Any

from sincpro_framework.data_layer.object_storage.domain import exceptions
from sincpro_framework.data_layer.object_storage.domain.object_store import (
    ObjectRef,
    ObjectStore,
)

DEFAULT_CONTENT_TYPE = "application/octet-stream"
"""What an object is when nothing says — no type given, none guessed from its key."""


class Objects:
    """Objects on one `ObjectStore`."""

    def __init__(self, store: ObjectStore) -> None:
        """`store` holds the bytes."""
        self.store = store

    def put(
        self,
        key: str,
        data: bytes,
        content_type: str | None = None,
        metadata: Mapping[str, str] | None = None,
        if_absent: bool = False,
        **options: Any,
    ) -> ObjectRef:
        """Store `data` under `key` with `metadata` — its content type the one given, else the
        one its key's extension says, else `application/octet-stream`. In the place of what was
        there, or, `if_absent`, only when nothing is: `ObjectAlreadyExists` then. `options` the
        storage's own, as they are."""
        said = dict(metadata or {})
        content_type = content_type or mimetypes.guess_type(key)[0] or DEFAULT_CONTENT_TYPE
        if not self.store.put(key, data, content_type, said, if_absent, **options):
            raise exceptions.ObjectAlreadyExists(f"an object is already stored under {key}")
        return ObjectRef(key=key, size=len(data), content_type=content_type, metadata=said)

    def get(self, key: str) -> bytes:
        """The bytes under `key` — `ObjectNotFound` when there are none."""
        data = self.store.get(key)
        if data is None:
            raise exceptions.ObjectNotFound(f"no object is stored under {key}")
        return data

    def find(self, key: str) -> ObjectRef | None:
        """What is stored under `key`, without its bytes — `None` when nothing is."""
        return self.store.info(key)

    def exists(self, key: str) -> bool:
        """Whether an object is stored under `key`."""
        return self.store.info(key) is not None

    def delete(self, key: str) -> None:
        """Drop what is under `key`; nothing there is not an error."""
        self.store.delete(key)

    def keys(self, prefix: str = "") -> Iterator[str]:
        """Every key that starts with `prefix`."""
        return self.store.keys(prefix)
