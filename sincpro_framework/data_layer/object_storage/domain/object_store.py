"""`ObjectStore`: where the bytes of an object live — a bucket, a disk, the process's memory — and what
an object is to whoever asks for it (`ObjectRef`).

    store.put("exports/invoices.csv", data, "text/csv", {"by": "ana"}, False, None)  ->  True
    store.put("exports/invoices.csv", data, "text/csv", {}, True, None)             ->  False, it is there
    store.get("exports/invoices.csv")          ->  StoredObject(data=b"...", ref=ObjectRef(…))
    store.info("exports/invoices.csv")         ->  ObjectRef(size=812, content_type="text/csv", metadata={"by": "ana"})
    list(store.keys("exports/"))               ->  ["exports/invoices.csv"]
    store.stream("exports/invoices.csv")       ->  ObjectStream: the bytes by parts, and its ref
    store.presign("exports/invoices.csv", Access.READ, timedelta(minutes=1))
        ->  "https://…" from a bucket, `None` from a store that cannot serve the bytes itself

Context: a key is the caller's — the store knows nothing of who stores what, nor why. Five
operations, what every object storage does, and two a store answers by default: `stream` reads
`get`'s bytes from memory until the store streams them (a bucket its response's body, the disk the
open file), `presign` is `None` until the store can hand the bytes out itself. A store keeps
`expires_at` and answers it; judging an object past it as gone is the services', so every store
follows one rule. `options` are the storage's own, passed as they are — headers, an ACL, a cache
control — so nothing a storage offers is out of reach; a store with no use for them ignores them.
"""

import io
from abc import ABC, abstractmethod
from collections.abc import Generator, Iterator, Mapping
from datetime import datetime, timedelta
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

    def expired(self, now: datetime) -> bool:
        """Whether it stopped being there by `now` — what every read judges before it answers."""
        return self.expires_at is not None and self.expires_at <= now


class StoredObject(DataTransferObject):
    """An object with its bytes: what `open` and `take` answer, in one read of the store."""

    data: bytes
    ref: ObjectRef


PART_SIZE = 1024 * 1024
"""The bytes `ObjectStream.chunks` yields at a time when no size is given: 1 MiB."""


class ObjectStream(io.BufferedReader):
    """An object read by parts: a binary file of the standard library, with what the object is
    (`ref`) — what `csv`, `hashlib.file_digest`, `shutil.copyfileobj` and `put` take as they are.

        with storage.stream("exports/movements.csv") as exported:
            rows = csv.DictReader(io.TextIOWrapper(exported, "utf-8"))
        StreamingResponse(storage.stream(key).chunks())     closed when the client has it all

    Only the part being read is in memory; closing it closes the store's response or file.
    """

    def __init__(self, source: Any, ref: ObjectRef) -> None:
        """`source` the store's bytes as they arrive — a response's body, an open file, a
        `BytesIO` — and `ref` what they are."""
        super().__init__(source)
        self.ref = ref

    def chunks(self, size: int = PART_SIZE) -> Generator[bytes, None, None]:
        """The bytes `size` at a time to the end — closed when they end, or when what iterates
        them stops: what a streaming response hands the client after the route has returned.
        """
        try:
            while part := self.read(size):
                yield part
        finally:
            self.close()


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

    def stream(self, key: str, **options: Any) -> ObjectStream | None:
        """The object under `key` to read by parts — `None` when there is none; `options` the
        storage's own (a range, a version). By default `get`'s bytes, all in memory: a store
        that streams answers its own."""
        found = self.get(key, **options)
        return None if found is None else ObjectStream(io.BytesIO(found.data), found.ref)

    def presign(
        self, key: str, access: Access, expires_in: timedelta, **options: Any
    ) -> str | None:
        """A URL the storage itself serves `key` at, to `access` it, for `expires_in` —
        `options` among its parameters. By default `None`: a store that cannot (memory, a
        disk) leaves the bytes to the service."""
        return None
