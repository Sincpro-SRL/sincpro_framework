"""`Contents`: an object's bytes loaded whole — opened in one read, gone once expired, and taken once
of many callers at once."""

import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from sincpro_framework.data_layer.object_storage import InMemoryObjectStore, ObjectNotFound
from sincpro_framework.data_layer.object_storage.services.contents import Contents
from sincpro_framework.data_layer.object_storage.services.objects import Objects
from sincpro_framework.runtime.testing import ManualClock, fresh_context

NOON = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _own_context() -> Iterator[None]:
    with fresh_context():
        yield


def test_an_object_is_opened_with_what_it_is_in_one_read():
    store = InMemoryObjectStore()
    Objects(store).put("exports/invoices.csv", b"a,b\n", "text/csv", {"filename": "f.csv"})
    contents = Contents(store)

    opened = contents.open("exports/invoices.csv")

    assert (opened.data, opened.ref.content_type) == (b"a,b\n", "text/csv")
    assert contents.get("exports/invoices.csv") == b"a,b\n"


def test_a_missing_key_is_not_found():
    with pytest.raises(ObjectNotFound):
        Contents(InMemoryObjectStore()).open("never")


def test_an_object_past_its_time_is_gone_and_deleted():
    clock = ManualClock(NOON)
    store = InMemoryObjectStore()
    Objects(store, now=clock.now).put(
        "inbox/scan.pdf", b"%PDF", expires_in=timedelta(hours=1)
    )
    contents = Contents(store, now=clock.now)

    clock.advance(hours=1)

    with pytest.raises(ObjectNotFound):
        contents.open("inbox/scan.pdf")
    assert store.info("inbox/scan.pdf") is None


def test_an_object_taken_is_gone_and_can_be_stored_and_taken_again():
    store = InMemoryObjectStore()
    objects, contents = Objects(store), Contents(store)
    objects.put("inbox/scan.pdf", b"%PDF", metadata={"filename": "scan.pdf"})

    taken = contents.take("inbox/scan.pdf")

    assert (taken.data, taken.ref.metadata) == (b"%PDF", {"filename": "scan.pdf"})
    assert not objects.exists("inbox/scan.pdf")
    with pytest.raises(ObjectNotFound):
        contents.take("inbox/scan.pdf")
    objects.put("inbox/scan.pdf", b"%PDF-2")
    assert contents.take("inbox/scan.pdf").data == b"%PDF-2"


class SlowStore(InMemoryObjectStore):
    """Holds every read a moment, so callers taking at once overlap."""

    def get(self, key, **options):
        threading.Event().wait(0.02)
        return super().get(key, **options)


def test_of_many_callers_taking_one_object_at_once_exactly_one_gets_it():
    store = SlowStore()
    Objects(store).put("inbox/scan.pdf", b"%PDF")
    contents = Contents(store)
    taken: list[bytes] = []
    missed: list[str] = []

    def take() -> None:
        try:
            taken.append(contents.take("inbox/scan.pdf").data)
        except ObjectNotFound as error:
            missed.append(str(error))

    threads = [threading.Thread(target=take) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert taken == [b"%PDF"] and len(missed) == 7
