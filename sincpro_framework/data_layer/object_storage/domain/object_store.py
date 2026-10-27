"""`ObjectStore`: where the bytes of an object live — a bucket, a disk, the process's memory — and what
an object is to whoever asks for it (`ObjectRef`).

    store.put("exports/invoices.csv", data, "text/csv", {"by": "ana"}, False, None)  ->  True
    store.put("exports/invoices.csv", data, "text/csv", {}, True, None)             ->  False, it is there
    store.get("exports/invoices.csv")          ->  StoredObject(data=b"...", ref=ObjectRef(…))
    store.info("exports/invoices.csv")         ->  ObjectRef(size=812, content_type="text/csv", metadata={"by": "ana"})
    list(store.keys("exports/"))               ->  ["exports/invoices.csv"]

Context: a key is the caller's — the store knows nothing of who stores what, nor why. Five
operations, what every object storage does. A store keeps `expires_at` and answers it; judging an
object past it as gone is `Objects`'s, so every store follows one rule; what only one technology offers (a bucket's presigned
URL) is a method of its adapter, reached by typing the adapter. `options` are the storage's own,
passed as they are — headers, an ACL, a cache control — so nothing a storage offers is out of
reach; a store with no use for them ignores them.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any, BinaryIO

from sincpro_framework.sincpro_abstractions import DataTransferObject


class Access(StrEnum):
    """What a publication lets its holder do with an object."""

    READ = "read"
    """Get its bytes."""
    WRITE = "write"
    """Put bytes under its key."""


class ObjectRef(DataTransferObject):
    """An object without its bytes: its key, what it is, and what was said of it."""

    key: str
    size: int
    content_type: str = "application/octet-stream"
    metadata: dict[str, str] = {}
    """What the caller stored beside it — a filename, an owner, a status."""
    modified_at: datetime | None = None
    """When it was last put — `None` from a store that does not say."""
    expires_at: datetime | None = None
    """When it stops being there — `None`, never."""


class StoredObject(DataTransferObject):
    """An object with its bytes: what `open` and `take` answer, in one read of the store."""

    data: bytes
    ref: ObjectRef


class ObjectStore(ABC):
    """Where objects are stored, by key."""

    @abstractmethod
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
        """Store `data` under `key` with `metadata` — bytes, or a file-like read to its end (a
        body spooled to disk: never all in memory where the store streams) — `options` the
        storage's own. In the place of what was there, or, `if_absent`, only when nothing is:
        `False` then, and nothing written. Atomic: of two puts `if_absent` at once, one
        stores. `expires_at` is kept and answered back in `ObjectRef.expires_at`."""

    @abstractmethod
    def get(self, key: str, **options: Any) -> StoredObject | None:
        """The bytes under `key` and what they are, in one read — `None` when there are none;
        `options` the storage's own (a version, a range, the customer key it was encrypted
        with)."""

    @abstractmethod
    def info(self, key: str, **options: Any) -> ObjectRef | None:
        """What is stored under `key`, without its bytes — `None` when nothing is; `options` the
        storage's own."""

    @abstractmethod
    def delete(self, key: str, **options: Any) -> None:
        """Drop what is under `key`; nothing there is not an error. `options` the storage's
        own (a version, a governance bypass)."""

    @abstractmethod
    def keys(self, prefix: str, **options: Any) -> Iterator[str]:
        """Every key that starts with `prefix` — `""` for all of them; `options` the storage's
        own. A store that knows an object's `expires_at` without reading it leaves the expired
        out; one that does not (a bucket's listing) may answer them until they are cleaned."""
