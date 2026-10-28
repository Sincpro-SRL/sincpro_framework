"""`Transfers`: an object's bytes passed on without holding them — by parts through the service, or
straight from the storage when it can serve them itself.

    transfers = Transfers(store)
    with transfers.stream("exports/movements.csv") as exported:    ObjectStream: one part in memory
        for part in exported.chunks(): ...
    transfers.direct("videos/raw.mov", Access.READ, timedelta(minutes=1))
        ->  "https://…" from a bucket: the client gets the bytes there — None from memory or a disk

Context: what a download, an upload handed on, a copy to another store or a hash of a large
object takes — the service holds one part at a time, or none. `direct` is the storage's own URL
when it has one (`ObjectStore.presign`), so a route answers a redirect when it is there and
streams when it is not, the same on every store. An object past its `expires_at` is gone from
both; a URL to write is handed out with nothing to check.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

from sincpro_framework.data_layer.object_storage.domain import exceptions
from sincpro_framework.data_layer.object_storage.domain.object_store import (
    Access,
    ObjectStore,
    ObjectStream,
)


class Transfers:
    """The bytes of the objects on one `ObjectStore`, passed on."""

    def __init__(
        self,
        store: ObjectStore,
        now: Callable[[], datetime] = partial(datetime.now, UTC),
    ) -> None:
        """`store` holds the bytes; `now` the clock objects expire by."""
        self.store = store
        self.now = now

    def stream(self, key: str, **options: Any) -> ObjectStream:
        """The object under `key` to read by parts, with what it is — `ObjectNotFound` when
        there is none, or it expired (and then it is deleted)."""
        found = self.store.stream(key, **options)
        if found is not None and found.ref.expired(self.now()):
            found.close()
            self.store.delete(key)
            found = None
        if found is None:
            raise exceptions.ObjectNotFound(f"no object is stored under {key}")
        return found

    def direct(
        self, key: str, access: Access, expires_in: timedelta, **options: Any
    ) -> str | None:
        """A URL the storage itself serves `key` at, to `access` it, for `expires_in` — `None`
        when the store cannot, and the bytes go through the service (`stream`). To read,
        `ObjectNotFound` when nothing is under `key`, or it expired (and then it is deleted).
        """
        url = self.store.presign(key, access, expires_in, **options)
        if url is None or access is Access.WRITE:
            return url
        found = self.store.info(key)
        if found is not None and found.expired(self.now()):
            self.store.delete(key)
            found = None
        if found is None:
            raise exceptions.ObjectNotFound(f"no object is stored under {key}")
        return url
