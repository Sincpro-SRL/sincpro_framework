"""`Objects`: the rules over a store — a missing key, a taken one, an expired one, a content type not
given, a metadata name every storage answers back, and an object taken once."""

import io
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from sincpro_framework.data_layer.object_storage import (
    InMemoryObjectStore,
    ObjectAlreadyExists,
    ObjectNotFound,
)
from sincpro_framework.data_layer.object_storage.services.objects import Objects
from sincpro_framework.exceptions import ProgrammingError
from sincpro_framework.runtime.testing import ManualClock, fresh_context

NOON = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _own_context() -> Iterator[None]:
    with fresh_context():
        yield


def test_an_object_is_stored_found_and_opened_by_its_key():
    objects = Objects(InMemoryObjectStore())

    stored = objects.put("exports/invoices.csv", b"a,b\n", "text/csv", {"filename": "f.csv"})
    opened = objects.open("exports/invoices.csv")

    assert (stored.size, stored.metadata) == (4, {"filename": "f.csv"})
    assert (opened.data, opened.ref.content_type) == (b"a,b\n", "text/csv")
    assert objects.get("exports/invoices.csv") == b"a,b\n"
    assert objects.exists("exports/invoices.csv") and not objects.exists("never")
    assert list(objects.keys("exports/")) == ["exports/invoices.csv"]


def test_a_missing_key_is_not_found_from_open_and_none_from_find():
    objects = Objects(InMemoryObjectStore())

    with pytest.raises(ObjectNotFound):
        objects.open("never")
    assert objects.find("never") is None


def test_a_content_type_not_given_is_the_one_its_key_says():
    objects = Objects(InMemoryObjectStore())

    assert objects.put("scan.pdf", b"%PDF").content_type == "application/pdf"
    assert objects.put("blob", b"x").content_type == "application/octet-stream"


def test_a_file_like_is_streamed_and_answered_with_its_stored_size():
    objects = Objects(InMemoryObjectStore())

    stored = objects.put("scan.pdf", io.BytesIO(b"%PDF-1.7"))

    assert (stored.size, stored.content_type) == (8, "application/pdf")


def test_a_put_if_absent_under_a_taken_key_is_already_exists():
    objects = Objects(InMemoryObjectStore())
    objects.put("claims/u-1", b"", if_absent=True)

    with pytest.raises(ObjectAlreadyExists):
        objects.put("claims/u-1", b"", if_absent=True)


@pytest.mark.parametrize("name", ["file_name", "FileName", "nombre ñ", ""])
def test_a_metadata_name_a_storage_would_answer_otherwise_is_refused(name):
    objects = Objects(InMemoryObjectStore())

    with pytest.raises(ProgrammingError, match="would not come back as written"):
        objects.put("a.pdf", b"%PDF", metadata={name: "x"})


def test_an_object_past_its_time_is_gone_from_every_read():
    clock = ManualClock(NOON)
    store = InMemoryObjectStore()
    objects = Objects(store, now=clock.now)

    stored = objects.put("inbox/scan.pdf", b"%PDF", expires_in=timedelta(hours=1))
    assert stored.expires_at == NOON + timedelta(hours=1)
    assert objects.exists("inbox/scan.pdf")

    clock.advance(hours=1)
    assert objects.find("inbox/scan.pdf") is None
    with pytest.raises(ObjectNotFound):
        objects.open("inbox/scan.pdf")
    assert store.info("inbox/scan.pdf") is None  # deleted once seen expired


def test_an_object_taken_is_gone_and_can_be_stored_and_taken_again():
    objects = Objects(InMemoryObjectStore())
    objects.put("inbox/scan.pdf", b"%PDF", metadata={"filename": "scan.pdf"})

    taken = objects.take("inbox/scan.pdf")

    assert (taken.data, taken.ref.metadata) == (b"%PDF", {"filename": "scan.pdf"})
    assert not objects.exists("inbox/scan.pdf")
    with pytest.raises(ObjectNotFound):
        objects.take("inbox/scan.pdf")
    objects.put("inbox/scan.pdf", b"%PDF-2")
    assert objects.take("inbox/scan.pdf").data == b"%PDF-2"


class SlowStore(InMemoryObjectStore):
    """Holds every read a moment, so callers taking at once overlap."""

    def get(self, key, **options):
        threading.Event().wait(0.02)
        return super().get(key, **options)


def test_of_many_callers_taking_one_object_at_once_exactly_one_gets_it():
    objects = Objects(SlowStore())
    objects.put("inbox/scan.pdf", b"%PDF")
    taken: list[bytes] = []
    missed: list[str] = []

    def take() -> None:
        try:
            taken.append(objects.take("inbox/scan.pdf").data)
        except ObjectNotFound as error:
            missed.append(str(error))

    threads = [threading.Thread(target=take) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert taken == [b"%PDF"] and len(missed) == 7
