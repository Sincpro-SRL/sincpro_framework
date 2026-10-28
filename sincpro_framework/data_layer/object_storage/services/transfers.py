"""`Transfers`: an object's bytes passed on without holding them — by parts through the service,
straight from the storage when it can serve them itself, or from a URL straight into the store.

    transfers = Transfers(store)
    with transfers.stream("exports/movements.csv") as exported:    ObjectStream: one part in memory
        for part in exported.chunks(): ...
    transfers.direct("videos/raw.mov", Access.READ, timedelta(minutes=1))
        ->  "https://…" from a bucket: the client gets the bytes there — None from memory or a disk
    transfers.fetch("https://cdn.acme.bo/scan.pdf", "inbox/scan.pdf", max_bytes=50 * MB)
        ->  ObjectRef: the URL's bytes streamed into the store, refused past 50 MB or 30 s

Context: what a download, an upload handed on, a copy to another store or a hash of a large
object takes — the service holds one part at a time, or none. `direct` is the storage's own URL
when it has one (`ObjectStore.presign`), so a route answers a redirect when it is there and
streams when it is not, the same on every store. An object past its `expires_at` is gone from
both; a URL to write is handed out with nothing to check. `fetch` reads a URL somebody gave — a
document a person has a link to — into the store a part at a time, by `Objects.put`'s rules,
within the bounds it is given: a size, a time, redirects followed or not, the hosts it takes. A
fetch refused or failed leaves nothing under the key that was not there before.
"""

import io
from collections.abc import Callable, Collection, Mapping
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any
from urllib.parse import urlsplit

from sincpro_framework.data_layer.object_storage.adapters.http_source import HttpSource
from sincpro_framework.data_layer.object_storage.domain import exceptions
from sincpro_framework.data_layer.object_storage.domain.fetch import FetchRefusal
from sincpro_framework.data_layer.object_storage.domain.object_store import (
    Access,
    ObjectRef,
    ObjectStore,
    ObjectStream,
)
from sincpro_framework.data_layer.object_storage.services.objects import Objects

FETCH_TIMEOUT = timedelta(seconds=30)
"""How long a fetch is given in all — connecting, and every byte — when none is said."""


class Transfers:
    """The bytes of the objects on one `ObjectStore`, passed on."""

    def __init__(
        self,
        store: ObjectStore,
        now: Callable[[], datetime] = partial(datetime.now, UTC),
        objects: Objects | None = None,
        source: HttpSource | None = None,
    ) -> None:
        """`store` holds the bytes; `now` the clock objects expire by; `objects` puts what is
        fetched, by its rules — its own over `store` when `None`; `source` opens the URLs."""
        self.store = store
        self.now = now
        self.objects = objects or Objects(store, now)
        self.source = source or HttpSource()

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
        """The bytes of `url` stored under `key`, a part at a time, never all in memory — its
        content type the one given, else the one its server says; `metadata`, `expires_in`
        and `options` as `put` takes them; `headers` sent with the request.

        1. `url` judged by its rules: one of `schemes` and one of `allowed_hosts`, each when given —
           and, when `follow_redirects`, so is every redirect's URL; `FetchRefused` otherwise.
        2. Opened within `timeout` (`source`) — refused by its `Content-Length` past
           `max_bytes`, a redirect not followed or a client error; `FetchFailed` for a server
           that fails, cannot be reached or runs out of time.
        3. Streamed into the store (`Objects.put`), counted as the store reads: past
           `max_bytes`, or past `timeout` in all, refused on the spot.
        Final: what was stored — or, refused or failed, nothing under `key` that was not
        there before, and the fetch's own refusal raised, whatever the store wrapped it in.
        """
        hosts = None if allowed_hosts is None else {host.lower() for host in allowed_hosts}

        def judged(candidate: str) -> None:
            """Refuse a URL whose scheme or host the fetch does not take."""
            parts = urlsplit(candidate)
            if schemes is not None and parts.scheme not in schemes:
                raise exceptions.FetchRefused(
                    FetchRefusal.SCHEME_NOT_ALLOWED,
                    candidate,
                    f"{candidate} is not {' or '.join(sorted(schemes))}",
                )
            if hosts is not None and (parts.hostname or "") not in hosts:
                raise exceptions.FetchRefused(
                    FetchRefusal.HOST_NOT_ALLOWED,
                    candidate,
                    f"{parts.hostname} is not one of the hosts this fetch takes",
                )

        judged(url)
        existed = self.store.info(key) is not None
        body = self.source.open(
            url, timeout, headers or {}, follow_redirects, judged, max_bytes
        )
        try:
            with io.BufferedReader(body) as streamed:
                return self.objects.put(
                    key,
                    streamed,
                    content_type or body.content_type,
                    metadata,
                    False,
                    expires_in,
                    **options,
                )
        except Exception as error:
            if not existed:
                self.store.delete(key)
            if body.refusal is not None and body.refusal is not error:
                raise body.refusal from error
            raise
