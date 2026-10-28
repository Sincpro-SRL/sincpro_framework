"""`Contents`: an object's bytes loaded whole, for a use case to process them — an OCR engine, a
DataFrame, an attachment encoded for another system — read once, or read once and gone.

    contents = Contents(store)
    contents.open("exports/invoices.csv")      ->  StoredObject(data, ref): one read of the store
    contents.get("exports/invoices.csv")       ->  b"..."
    contents.take("inbox/scan.pdf")            ->  StoredObject, and it is gone — once, on any store
    contents.get("never")                      ->  ObjectNotFound

Context: the whole object is in memory — what a use case that needs all of it takes; one that
hands the bytes on, or reads them by parts, streams them (`Transfers`). An object past its
`expires_at` is gone. `take` claims the key in the `KeyValueStore` the context stands on (`add`, a
`SET NX` on Redis or Valkey) before it reads and deletes, so of two replicas taking one object,
one gets it — whatever the storage offers; a claim lasts a minute at most, so a process that dies
holding one never locks the object for good.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

from sincpro_framework.context import ContextStore, use_context
from sincpro_framework.context.entrypoint.resolution import key_value_store_of
from sincpro_framework.data_layer.object_storage.domain import exceptions
from sincpro_framework.data_layer.object_storage.domain.object_store import (
    ObjectStore,
    StoredObject,
)

CLAIMS = "sincpro:object_claim"
"""The section of the `KeyValueStore` a `take` claims its key in."""

CLAIM_TTL = timedelta(minutes=1)
"""The longest a claim is held — what a process that dies while taking leaves behind."""


class Contents:
    """The bytes of the objects on one `ObjectStore`, loaded whole."""

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

    def open(self, key: str, **options: Any) -> StoredObject:
        """The bytes under `key` and what they are, in one read of the store — `ObjectNotFound`
        when there are none, or they expired (and then they are deleted)."""
        found = self.store.get(key, **options)
        if found is not None and found.ref.expired(self.now()):
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
