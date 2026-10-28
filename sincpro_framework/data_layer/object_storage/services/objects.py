"""`Objects`: what is stored, by key — put, found, listed, deleted — with the rules over the port, the same
on every store: a taken key under `if_absent` is `ObjectAlreadyExists`, an object past its
`expires_at` is gone, a content type not given is guessed from the key, and a metadata name is
lowercase letters, digits and `-`.

    objects = Objects(store)
    objects.put("exports/invoices.csv", data, expires_in=timedelta(hours=1))
        ->  ObjectRef(content_type="text/csv", expires_at=…)
    objects.put("videos/raw.mov", a_file)     ->  the file read to its end, streamed where the store streams
    objects.find("exports/invoices.csv")      ->  ObjectRef, without its bytes — None when it is not there
    list(objects.keys("exports/"))            ->  ["exports/invoices.csv"]

Context: the bytes are read by `Contents` (whole, to process them) and passed on by `Transfers`
(by parts, or by the storage itself); this is what is there. A metadata name is held to what
every storage answers back as written: AWS lowercases names, and Ceph RGW (DigitalOcean Spaces)
answers a `_` as `-`.
"""

import mimetypes
import re
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any, BinaryIO

from sincpro_framework.data_layer.object_storage.domain import exceptions
from sincpro_framework.data_layer.object_storage.domain.object_store import (
    ObjectRef,
    ObjectStore,
)

DEFAULT_CONTENT_TYPE = "application/octet-stream"
"""What an object is when nothing says — no type given, none guessed from its key."""

METADATA_NAME = re.compile(r"[a-z0-9-]+")
"""A metadata name every storage answers back as written."""


class Objects:
    """What is stored on one `ObjectStore`."""

    def __init__(
        self,
        store: ObjectStore,
        now: Callable[[], datetime] = partial(datetime.now, UTC),
    ) -> None:
        """`store` holds the bytes; `now` the clock objects expire by."""
        self.store = store
        self.now = now

    def put(
        self,
        key: str,
        data: bytes | BinaryIO,
        content_type: str | None = None,
        metadata: Mapping[str, str] | None = None,
        if_absent: bool = False,
        expires_in: timedelta | None = None,
        **options: Any,
    ) -> ObjectRef:
        """Store `data` — bytes, or a file-like streamed to the store — under `key` with
        `metadata`, until `expires_in` from now when given; its content type the one given,
        else the one its key's extension says, else `application/octet-stream`. In the place of
        what was there, or, `if_absent`, only when nothing is: `ObjectAlreadyExists` then.
        `options` the storage's own, as they are.

        1. A metadata name outside lowercase letters, digits and `-` is a `ProgrammingError`.
        2. The store writes; `False` under `if_absent` is `ObjectAlreadyExists`.
        Final: what was stored — its size read back from the store for a file-like.
        """
        said = dict(metadata or {})
        wrong = [name for name in said if not METADATA_NAME.fullmatch(name)]
        if wrong:
            raise exceptions.ProgrammingError(
                f"metadata names {wrong} would not come back as written: use lowercase letters, "
                "digits and '-' (AWS lowercases names; Ceph RGW answers '_' as '-')"
            )
        content_type = content_type or mimetypes.guess_type(key)[0] or DEFAULT_CONTENT_TYPE
        expires_at = self.now() + expires_in if expires_in is not None else None
        if not self.store.put(
            key, data, content_type, said, if_absent, expires_at, **options
        ):
            raise exceptions.ObjectAlreadyExists(f"an object is already stored under {key}")
        if isinstance(data, bytes):
            return ObjectRef(
                key=key,
                size=len(data),
                content_type=content_type,
                metadata=said,
                expires_at=expires_at,
            )
        stored = self.store.info(key)
        return stored if stored is not None else ObjectRef(key=key, size=0, metadata=said)

    def find(self, key: str, **options: Any) -> ObjectRef | None:
        """What is stored under `key`, without its bytes — `None` when nothing is, or it
        expired (and then it is deleted)."""
        found = self.store.info(key, **options)
        if found is not None and found.expired(self.now()):
            self.store.delete(key)
            return None
        return found

    def exists(self, key: str, **options: Any) -> bool:
        """Whether an object is stored under `key`, and has not expired."""
        return self.find(key, **options) is not None

    def delete(self, key: str, **options: Any) -> None:
        """Drop what is under `key`; nothing there is not an error."""
        self.store.delete(key, **options)

    def keys(self, prefix: str = "", **options: Any) -> Iterator[str]:
        """Every key that starts with `prefix` — a bucket's listing may still name one that
        expired, until its lifecycle rule cleans it."""
        return self.store.keys(prefix, **options)
