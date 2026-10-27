"""`Objects`: storing and reading objects by key — the rules over the port, the same on every store:
a missing key is `ObjectNotFound`, a taken one under `if_absent` is `ObjectAlreadyExists`, an object
past its `expires_at` is gone, a content type not given is guessed from the key, and a metadata
name is lowercase letters, digits and `-`.

    objects = Objects(store)
    objects.put("exports/invoices.csv", data, expires_in=timedelta(hours=1))
        ->  ObjectRef(content_type="text/csv", expires_at=…)
    objects.open("exports/invoices.csv")      ->  StoredObject(data, ref): one read of the store
    objects.take("inbox/scan.pdf")            ->  StoredObject, and it is gone — once, on any store
    objects.get("never")                      ->  ObjectNotFound

Context: `take` claims the key in the `KeyValueStore` the context stands on (`add`, a `SET NX` on
Redis or Valkey) before it reads and deletes, so of two replicas taking one object, one gets it —
whatever the storage offers; a claim lasts a minute at most, so a process that dies holding one
never locks the object for good. A metadata name is held to what every storage answers back as
written: AWS lowercases names, and Ceph RGW (DigitalOcean Spaces) answers a `_` as `-`.
"""

import mimetypes
import re
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any, BinaryIO

from sincpro_framework.context import ContextStore, use_context
from sincpro_framework.context.entrypoint.resolution import key_value_store_of
from sincpro_framework.data_layer.object_storage.domain import exceptions
from sincpro_framework.data_layer.object_storage.domain.object_store import (
    ObjectRef,
    ObjectStore,
    StoredObject,
)

DEFAULT_CONTENT_TYPE = "application/octet-stream"
"""What an object is when nothing says — no type given, none guessed from its key."""

METADATA_NAME = re.compile(r"[a-z0-9-]+")
"""A metadata name every storage answers back as written."""

CLAIMS = "sincpro:object_claim"
"""The section of the `KeyValueStore` a `take` claims its key in."""

CLAIM_TTL = timedelta(minutes=1)
"""The longest a claim is held — what a process that dies while taking leaves behind."""


class Objects:
    """Objects on one `ObjectStore`."""

    def __init__(
        self,
        store: ObjectStore,
        context_store: ContextStore | None = None,
        now: Callable[[], datetime] = partial(datetime.now, UTC),
    ) -> None:
        """`store` holds the bytes; `context_store` is where a `take` claims, else the
        context's store in play; `now` the clock objects expire by."""
        self.store = store
        self.context_store = context_store
        self.now = now

    def _expired(self, ref: ObjectRef) -> bool:
        """Whether `ref` is past its `expires_at`."""
        return ref.expires_at is not None and ref.expires_at <= self.now()

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
        if found is not None and self._expired(found):
            self.store.delete(key)
            return None
        return found

    def exists(self, key: str, **options: Any) -> bool:
        """Whether an object is stored under `key`, and has not expired."""
        return self.find(key, **options) is not None

    def open(self, key: str, **options: Any) -> StoredObject:
        """The bytes under `key` and what they are, in one read of the store — `ObjectNotFound`
        when there are none, or they expired (and then they are deleted)."""
        found = self.store.get(key, **options)
        if found is not None and self._expired(found.ref):
            self.store.delete(key)
            found = None
        if found is None:
            raise exceptions.ObjectNotFound(f"no object is stored under {key}")
        return found

    def get(self, key: str, **options: Any) -> bytes:
        """The bytes under `key` — `ObjectNotFound` when there are none, or they expired."""
        return self.open(key, **options).data

    def take(self, key: str, **options: Any) -> StoredObject:
        """The object under `key`, deleted as it is read — of every caller taking it at once, on
        any replica, one gets it and the rest `ObjectNotFound`, as when it is not there.

        1. Claim `key` in the context's `KeyValueStore` (`add`); a claim held is
           `ObjectNotFound`.
        2. Open it, then delete it.
        Final: the claim is let go, so the key can be stored and taken again.
        """
        claims = key_value_store_of(use_context().store_in_use(self.context_store))
        claim = f"{CLAIMS}:{key}"
        if not claims.add(claim, b"", CLAIM_TTL):
            raise exceptions.ObjectNotFound(
                f"the object under {key} is being taken by another"
            )
        try:
            taken = self.open(key, **options)
            self.store.delete(key, **options)
            return taken
        finally:
            claims.delete(claim)

    def delete(self, key: str, **options: Any) -> None:
        """Drop what is under `key`; nothing there is not an error."""
        self.store.delete(key, **options)

    def keys(self, prefix: str = "", **options: Any) -> Iterator[str]:
        """Every key that starts with `prefix` — a bucket's listing may still name one that
        expired, until its lifecycle rule cleans it."""
        return self.store.keys(prefix, **options)
