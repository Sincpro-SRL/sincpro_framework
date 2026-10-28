"""`Objects`: what is stored, with the rules over a store — a taken key, an expired one, a content type
not given and a metadata name every storage answers back."""

import io
from datetime import UTC, datetime, timedelta

import pytest

from sincpro_framework.data_layer.object_storage import (
    InMemoryObjectStore,
    ObjectAlreadyExists,
)
from sincpro_framework.data_layer.object_storage.services.objects import Objects
from sincpro_framework.exceptions import ProgrammingError
from sincpro_framework.runtime.testing import ManualClock

NOON = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def test_an_object_is_stored_found_and_listed_by_its_key():
    objects = Objects(InMemoryObjectStore())

    stored = objects.put("exports/invoices.csv", b"a,b\n", "text/csv", {"filename": "f.csv"})
    found = objects.find("exports/invoices.csv")

    assert (stored.size, stored.metadata) == (4, {"filename": "f.csv"})
    assert found is not None and found.content_type == "text/csv"
    assert objects.exists("exports/invoices.csv") and not objects.exists("never")
    assert list(objects.keys("exports/")) == ["exports/invoices.csv"]
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


def test_an_object_past_its_time_is_not_found_and_deleted():
    clock = ManualClock(NOON)
    store = InMemoryObjectStore()
    objects = Objects(store, now=clock.now)

    stored = objects.put("inbox/scan.pdf", b"%PDF", expires_in=timedelta(hours=1))
    assert stored.expires_at == NOON + timedelta(hours=1)
    assert objects.exists("inbox/scan.pdf")

    clock.advance(hours=1)
    assert objects.find("inbox/scan.pdf") is None
    assert store.info("inbox/scan.pdf") is None  # deleted once seen expired
