"""`ObjectStorage`: the facade a use case stores objects with, and publishes them by an id — injected as
any adapter, over any `ObjectStore`. It decides nothing: each call goes to the service of its use
case.

    billing.add_dependency("storage", ObjectStorage(S3CompatibleObjectStore("documents", client), secret))

    self.storage.put("exports/invoices.csv", data)                    ->  ObjectRef(content_type="text/csv", …)
    self.storage.get("exports/invoices.csv")                          ->  b"...", whole, to process
    self.storage.stream("exports/invoices.csv")                       ->  ObjectStream, by parts, to pass on
    self.storage.direct("exports/invoices.csv", Access.READ, timedelta(minutes=1))
        ->  the storage's own URL, or None when the bytes go through the service
    self.storage.fetch("https://cdn.acme.bo/scan.pdf", "inbox/scan.pdf", max_bytes=50 * MB)
        ->  ObjectRef: a URL's bytes streamed into the store, within the bounds given
    published = self.storage.publish("exports/invoices.csv", Access.READ, timedelta(hours=1), max_uses=1)
    published.id                                                      what an entrypoint puts in its URL

    self.storage.check(id, Access.READ)     ->  Publication, nothing spent: a preview, a HEAD
    self.storage.redeem(id, Access.READ)    ->  Publication, one use spent — then open(published.key)
    self.storage.revoke(id)                     closed before its time

    ticket = self.storage.publish(key, Access.WRITE, timedelta(minutes=5), max_uses=1,
                                  then=Grant(access=Access.READ, expires_in=timedelta(hours=1), max_uses=1))
        one id: the client uploads with it once, then a use case claims the object with it once

Context: a publication is the mechanism, never a URL — how long, how many uses, to read or to
write, and what the publisher said of it (`metadata`). The entrypoint that exposes it builds its
URL from the id, and asks `redeem` when the id comes back.

    objects        Objects          put, find, exists, delete, keys — what is stored
    contents       Contents         open, get, take — the bytes whole, to process them
    transfers      Transfers        stream, direct, fetch — the bytes passed on without holding them
    ids            PublicationIds   the id, signed with the secret
    publications   Publications     publish, check, redeem, revoke — in the context's store

Each is an attribute a project reads or replaces — a subclass of its own, a store of its own —
and `store` is the `ObjectStore` itself, for what only its technology offers.
"""

from collections.abc import Callable, Collection, Iterator, Mapping
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any, BinaryIO

from sincpro_framework.context import ContextStore
from sincpro_framework.data_layer.object_storage.domain.object_store import (
    Access,
    ObjectRef,
    ObjectStore,
    ObjectStream,
    StoredObject,
)
from sincpro_framework.data_layer.object_storage.domain.publication import (
    Grant,
    Publication,
)
from sincpro_framework.data_layer.object_storage.services.contents import Contents
from sincpro_framework.data_layer.object_storage.services.ids import PublicationIds
from sincpro_framework.data_layer.object_storage.services.objects import Objects
from sincpro_framework.data_layer.object_storage.services.publications import (
    Publications,
)
from sincpro_framework.data_layer.object_storage.services.transfers import (
    FETCH_TIMEOUT,
    Transfers,
)


class ObjectStorage:
    """Objects on one `ObjectStore`, and publications of them by id."""

    def __init__(
        self,
        store: ObjectStore,
        secret: str | bytes | None = None,
        context_store: ContextStore | None = None,
        now: Callable[[], datetime] = partial(datetime.now, UTC),
    ) -> None:
        """`store` holds the bytes; `secret` signs every publication's id — the same on every
        replica; `context_store` is where publications and claims are saved, else the
        context's store in play when each call runs; `now` the clock objects and publications
        expire by."""
        self.store = store
        self.objects = Objects(store, now)
        self.contents = Contents(store, context_store, now)
        self.transfers = Transfers(store, now, self.objects)
        self.ids = PublicationIds(secret)
        self.publications = Publications(self.ids, context_store, now)

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
        """Store `data` under `key`, until `expires_in` when given — see `Objects.put`."""
        return self.objects.put(
            key, data, content_type, metadata, if_absent, expires_in, **options
        )

    def open(self, key: str, **options: Any) -> StoredObject:
        """The bytes under `key` and what they are, in one read — `ObjectNotFound` when there
        are none, or they expired."""
        return self.contents.open(key, **options)

    def get(self, key: str, **options: Any) -> bytes:
        """The bytes under `key` — `ObjectNotFound` when there are none, or they expired."""
        return self.contents.get(key, **options)

    def take(self, key: str, **options: Any) -> StoredObject:
        """The object under `key`, deleted as it is read — once, on any replica; the rest
        `ObjectNotFound`."""
        return self.contents.take(key, **options)

    def stream(self, key: str, **options: Any) -> ObjectStream:
        """The object under `key` to read by parts, one in memory at a time — `ObjectNotFound`
        when there is none, or it expired."""
        return self.transfers.stream(key, **options)

    def direct(
        self,
        key: str,
        access: Access = Access.READ,
        expires_in: timedelta = timedelta(minutes=1),
        **options: Any,
    ) -> str | None:
        """A URL the storage itself serves `key` at, to `access` it, for `expires_in` — `None`
        when the store cannot, and the bytes go through the service (`stream`)."""
        return self.transfers.direct(key, access, expires_in, **options)

    def fetch(
        self,
        url: str,
        key: str,
        content_type: str | None = None,
        metadata: Mapping[str, str] | None = None,
        expires_in: timedelta | None = None,
        max_bytes: int | None = None,
        timeout: timedelta = FETCH_TIMEOUT,
        follow_redirects: bool = False,
        allowed_hosts: Collection[str] | None = None,
        schemes: Collection[str] | None = None,
        headers: Mapping[str, str] | None = None,
        **options: Any,
    ) -> ObjectRef:
        """The bytes of `url` stored under `key`, a part at a time — refused past `max_bytes`,
        `timeout` in all, a redirect unless `follow_redirects`, a host not in `allowed_hosts`
        when given; nothing stored when refused — see `Transfers.fetch`."""
        return self.transfers.fetch(
            url,
            key,
            content_type,
            metadata,
            expires_in,
            max_bytes,
            timeout,
            follow_redirects,
            allowed_hosts,
            schemes,
            headers,
            **options,
        )

    def find(self, key: str, **options: Any) -> ObjectRef | None:
        """What is stored under `key`, without its bytes — `None` when nothing is, or it
        expired."""
        return self.objects.find(key, **options)

    def exists(self, key: str, **options: Any) -> bool:
        """Whether an object is stored under `key`."""
        return self.objects.exists(key, **options)

    def delete(self, key: str, **options: Any) -> None:
        """Drop what is under `key`; nothing there is not an error."""
        self.objects.delete(key, **options)

    def keys(self, prefix: str = "", **options: Any) -> Iterator[str]:
        """Every key that starts with `prefix`."""
        return self.objects.keys(prefix, **options)

    def publish(
        self,
        key: str,
        access: Access = Access.READ,
        expires_in: timedelta = timedelta(hours=1),
        max_uses: int | None = None,
        metadata: Mapping[str, Any] | None = None,
        then: Grant | None = None,
    ) -> Publication:
        """Make the object under `key` reachable by an id, to `access` it, for `expires_in`,
        `max_uses` times — every time with `None`, once with `1` — and, once those are spent,
        to `then` with the same id. `metadata` travels with it, for whoever checks or redeems
        it."""
        return self.publications.publish(
            key, access, expires_in, max_uses, metadata or {}, then
        )

    def check(self, id: str, access: Access) -> Publication:
        """The publication `id` names, when it opens for `access` — nothing spent: what a
        preview or a HEAD asks. `PublicationRefused`, with its `reason`, when it does not — and
        `check` spends nothing, so it is also how a download used without end is let in
        without a write."""
        return self.publications.opened(id, access, spend=False)

    def redeem(self, id: str, access: Access) -> Publication:
        """The publication `id` names, with one use spent, when it opens for `access` — what the
        entrypoint asks before it serves or takes the bytes of its `key`. `PublicationRefused`,
        with its `reason`, when it does not."""
        return self.publications.opened(id, access, spend=True)

    def revoke(self, id: str) -> None:
        """Close the publication `id` names before its time; one already gone is not an error."""
        self.publications.revoke(id)
