"""`Objects`: the rules over a store — a missing key, a taken one, a content type not given."""

import io

import pytest

from sincpro_framework.data_layer.object_storage import (
    InMemoryObjectStore,
    ObjectAlreadyExists,
    ObjectNotFound,
)
from sincpro_framework.data_layer.object_storage.services.objects import Objects


def test_an_object_is_stored_found_and_got_by_its_key():
    objects = Objects(InMemoryObjectStore())

    stored = objects.put("exports/invoices.csv", b"a,b\n", "text/csv", {"filename": "f.csv"})

    assert (stored.size, stored.metadata) == (4, {"filename": "f.csv"})
    assert objects.get("exports/invoices.csv") == b"a,b\n"
    found = objects.find("exports/invoices.csv")
    assert found is not None and found.content_type == "text/csv"
    assert objects.exists("exports/invoices.csv") and not objects.exists("never")
    assert list(objects.keys("exports/")) == ["exports/invoices.csv"]


def test_a_missing_key_is_not_found_from_get_and_none_from_find():
    objects = Objects(InMemoryObjectStore())

    with pytest.raises(ObjectNotFound):
        objects.get("never")
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
