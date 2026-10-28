"""`Transfers`: an object's bytes passed on without holding them — read by parts as a binary file of the
standard library, closed when its parts end, and served by the storage itself when it can."""

import csv
import hashlib
import io
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from sincpro_framework.data_layer.object_storage import (
    Access,
    InMemoryObjectStore,
    LocalObjectStore,
    ObjectNotFound,
    ObjectStream,
    S3CompatibleObjectStore,
)
from sincpro_framework.data_layer.object_storage.services.objects import Objects
from sincpro_framework.data_layer.object_storage.services.transfers import Transfers
from sincpro_framework.runtime.testing import ManualClock
from tests.data_layer.object_storage.test_stores import FakeS3

NOON = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
MOVEMENTS = b"account,amount\n" + b"".join(b"1101,%d\n" % n for n in range(20_000))


class Recorded(io.BytesIO):
    """A store's response that says how much was asked of it at once, and whether it was closed."""

    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.asked: list[int] = []
        self.was_closed = False

    def readinto(self, buffer: Any) -> int:
        self.asked.append(len(memoryview(buffer)))
        return super().readinto(buffer)

    def close(self) -> None:
        self.was_closed = True
        super().close()


class RecordingStore(InMemoryObjectStore):
    """Streams what it holds through a `Recorded` response."""

    def __init__(self) -> None:
        super().__init__()
        self.responses: list[Recorded] = []

    def stream(self, key: str, **options: Any) -> ObjectStream | None:
        found = self.get(key)
        if found is None:
            return None
        self.responses.append(Recorded(found.data))
        return ObjectStream(self.responses[-1], found.ref)


def test_an_object_is_read_by_parts_never_more_than_one_at_a_time():
    store = RecordingStore()
    Objects(store).put("exports/movements.csv", MOVEMENTS, "text/csv")

    parts = list(Transfers(store).stream("exports/movements.csv").chunks(64 * 1024))

    assert b"".join(parts) == MOVEMENTS
    assert max(len(part) for part in parts) == 64 * 1024
    assert max(store.responses[0].asked) == 64 * 1024
    assert store.responses[0].was_closed


def test_a_stream_is_a_binary_file_what_reads_a_file_takes_as_it_is(tmp_path: Path):
    store = LocalObjectStore(tmp_path / "objects")
    Objects(store).put("exports/movements.csv", MOVEMENTS, "text/csv", {"filename": "m.csv"})
    transfers = Transfers(store)

    with transfers.stream("exports/movements.csv") as exported:
        assert (exported.ref.size, exported.ref.metadata) == (
            len(MOVEMENTS),
            {"filename": "m.csv"},
        )
        rows = list(csv.DictReader(io.TextIOWrapper(exported, "utf-8")))
    with transfers.stream("exports/movements.csv") as exported:
        fingerprint = hashlib.file_digest(exported, "sha256").hexdigest()

    assert (len(rows), rows[-1]) == (20_000, {"account": "1101", "amount": "19999"})
    assert fingerprint == hashlib.sha256(MOVEMENTS).hexdigest()
    assert exported.closed


def test_an_object_streams_from_one_store_into_another(tmp_path: Path):
    source, archive = LocalObjectStore(tmp_path / "source"), FakeS3()
    Objects(source).put("exports/movements.csv", MOVEMENTS, "text/csv")
    bucket = S3CompatibleObjectStore("archive", archive)

    with Transfers(source).stream("exports/movements.csv") as exported:
        copied = Objects(bucket).put(
            "2026/movements.csv", exported, exported.ref.content_type
        )

    assert (copied.size, copied.content_type) == (len(MOVEMENTS), "text/csv")
    assert archive.objects["archive", "2026/movements.csv"]["Body"] == MOVEMENTS


def test_parts_left_unread_close_the_stream_when_what_iterates_them_stops():
    store = RecordingStore()
    Objects(store).put("exports/movements.csv", MOVEMENTS)

    parts = Transfers(store).stream("exports/movements.csv").chunks(1024)
    next(parts)
    parts.close()  # a client gone mid-download

    assert store.responses[0].was_closed


def test_a_missing_or_expired_object_is_not_found_and_the_expired_deleted():
    clock = ManualClock(NOON)
    store = InMemoryObjectStore()
    Objects(store, now=clock.now).put(
        "inbox/scan.pdf", b"%PDF", expires_in=timedelta(hours=1)
    )
    transfers = Transfers(store, now=clock.now)

    with pytest.raises(ObjectNotFound):
        transfers.stream("never")
    clock.advance(hours=1)
    with pytest.raises(ObjectNotFound):
        transfers.stream("inbox/scan.pdf")
    assert store.info("inbox/scan.pdf") is None


def test_a_store_that_cannot_serve_the_bytes_itself_answers_no_direct_url(tmp_path: Path):
    for store in (InMemoryObjectStore(), LocalObjectStore(tmp_path / "objects")):
        Objects(store).put("a.pdf", b"%PDF")

        assert Transfers(store).direct("a.pdf", Access.READ, timedelta(minutes=1)) is None


def test_a_bucket_serves_the_bytes_itself_by_its_own_url():
    store = S3CompatibleObjectStore("documents", FakeS3())
    Objects(store).put("a.pdf", b"%PDF")
    transfers = Transfers(store)

    read = transfers.direct(
        "a.pdf", Access.READ, timedelta(minutes=1), ResponseContentDisposition="attachment"
    )
    write = transfers.direct("inbox/new.pdf", Access.WRITE, timedelta(minutes=5))

    assert read == (
        "https://documents.s3/a.pdf?op=get_object&expires=60"
        "&ResponseContentDisposition=attachment"
    )
    assert write == "https://documents.s3/inbox/new.pdf?op=put_object&expires=300"


def test_a_direct_url_to_read_nothing_or_an_expired_object_is_not_found():
    clock = ManualClock(NOON)
    store = S3CompatibleObjectStore("documents", FakeS3())
    Objects(store, now=clock.now).put("a.pdf", b"%PDF", expires_in=timedelta(hours=1))
    transfers = Transfers(store, now=clock.now)

    with pytest.raises(ObjectNotFound):
        transfers.direct("never", Access.READ, timedelta(minutes=1))
    clock.advance(hours=1)
    with pytest.raises(ObjectNotFound):
        transfers.direct("a.pdf", Access.READ, timedelta(minutes=1))
    assert store.info("a.pdf") is None
