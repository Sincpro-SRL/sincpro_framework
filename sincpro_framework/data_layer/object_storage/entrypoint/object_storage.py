"""`ObjectStorage`: the facade a use case stores objects with, and publishes them by an id — injected as
any adapter, over any `ObjectStore`. It decides nothing: each call goes to the service of its use
case.

    billing.add_dependency("storage", ObjectStorage(S3CompatibleObjectStore("documents", client), secret))

    self.storage.put("exports/invoices.csv", data)                    ->  ObjectRef(content_type="text/csv", …)
    self.storage.get("exports/invoices.csv")                          ->  b"..."
    published = self.storage.publish("exports/invoices.csv", Access.READ, timedelta(hours=1), max_uses=1)
    published.id                                                      what an entrypoint puts in its URL

    self.storage.check(id, Access.READ)     ->  Publication, nothing spent: a preview, a HEAD
    self.storage.redeem(id, Access.READ)    ->  Publication, one use spent — then get(published.key)
    self.storage.revoke(id)                     closed before its time

Context: a publication is the mechanism, never a URL — how long, how many uses, to read or to
write, and what the publisher said of it (`metadata`). The entrypoint that exposes it builds its
URL from the id, and asks `redeem` when the id comes back.

    objects        Objects          put, get, find, exists, delete, keys
    ids            PublicationIds   the id, signed with the secret
    publications   Publications     publish, check, redeem, revoke — in the context's store

Each is an attribute a project reads or replaces — a subclass of its own, a store of its own —
and `store` is the `ObjectStore` itself, for what only its technology offers (a bucket's
`presign`).
"""

from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any, BinaryIO

from sincpro_framework.context import ContextStore
from sincpro_framework.data_layer.object_storage.domain.object_store import (
    Access,
    ObjectRef,
    ObjectStore,
)
from sincpro_framework.data_layer.object_storage.domain.publication import Publication
from sincpro_framework.data_layer.object_storage.services.ids import PublicationIds
from sincpro_framework.data_layer.object_storage.services.objects import Objects
from sincpro_framework.data_layer.object_storage.services.publications import (
    Publications,
)


class ObjectStorage:
    """Objects on one `ObjectStore`, and publications of them by id."""

    def __init__(
        self,
        store: ObjectStore,
        secret: str | bytes | None = None,
        publication_store: ContextStore | None = None,
        now: Callable[[], datetime] = partial(datetime.now, UTC),
    ) -> None:
        """`store` holds the bytes; `secret` signs every publication's id — the same on every
        replica; `publication_store` is where publications are saved, else the context's
        store; `now` the clock they expire by."""
        self.store = store
        self.objects = Objects(store)
        self.ids = PublicationIds(secret)
        self.publications = Publications(self.ids, publication_store, now)

    def put(
        self,
        key: str,
        data: bytes | BinaryIO,
        content_type: str | None = None,
        metadata: Mapping[str, str] | None = None,
        if_absent: bool = False,
        **options: Any,
    ) -> ObjectRef:
        """Store `data` under `key` — see `Objects.put`."""
        return self.objects.put(key, data, content_type, metadata, if_absent, **options)

    def get(self, key: str) -> bytes:
        """The bytes under `key` — `ObjectNotFound` when there are none."""
        return self.objects.get(key)

    def find(self, key: str) -> ObjectRef | None:
        """What is stored under `key`, without its bytes — `None` when nothing is."""
        return self.objects.find(key)

    def exists(self, key: str) -> bool:
        """Whether an object is stored under `key`."""
        return self.objects.exists(key)

    def delete(self, key: str) -> None:
        """Drop what is under `key`; nothing there is not an error."""
        self.objects.delete(key)

    def keys(self, prefix: str = "") -> Iterator[str]:
        """Every key that starts with `prefix`."""
        return self.objects.keys(prefix)

    def publish(
        self,
        key: str,
        access: Access = Access.READ,
        expires_in: timedelta = timedelta(hours=1),
        max_uses: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Publication:
        """Make the object under `key` reachable by an id, to `access` it, for `expires_in`,
        `max_uses` times — every time with `None`, once with `1`. `metadata` travels with it,
        for whoever checks or redeems it."""
        return self.publications.publish(key, access, expires_in, max_uses, metadata or {})

    def check(self, id: str, access: Access) -> Publication:
        """The publication `id` names, when it opens for `access` — nothing spent: what a
        preview or a HEAD asks. `PublicationRefused`, with its `reason`, when it does not."""
        return self.publications.opened(id, access, spend=False)

    def redeem(self, id: str, access: Access) -> Publication:
        """The publication `id` names, with one use spent, when it opens for `access` — what the
        entrypoint asks before it serves or takes the bytes of its `key`. `PublicationRefused`,
        with its `reason`, when it does not."""
        return self.publications.opened(id, access, spend=True)

    def revoke(self, id: str) -> None:
        """Close the publication `id` names before its time; one already gone is not an error."""
        self.publications.revoke(id)
