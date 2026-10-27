"""Every store keeps the `ObjectStore` contract; what each technology adds is its adapter's — a
bucket's prefix, its options on every put, its presigned URLs, its failures."""

import io
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from sincpro_framework.data_layer.object_storage import (
    Access,
    InMemoryObjectStore,
    LocalObjectStore,
    ObjectStore,
    S3CompatibleObjectStore,
)
from sincpro_framework.exceptions import ExternalServiceError, ProgrammingError
from sincpro_framework.runtime.testing import ObjectStoreContract

NOON = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


class S3Error(Exception):
    """What botocore raises: an error code in its response."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeS3:
    """The few calls of a boto3 S3 client `S3CompatibleObjectStore` makes."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], dict[str, Any]] = {}
        self.down = False

    def put_object(self, Bucket: str, Key: str, Body: bytes, **extra: Any) -> None:
        if self.down:
            raise S3Error("ServiceUnavailable")
        if extra.pop("IfNoneMatch", None) == "*" and (Bucket, Key) in self.objects:
            raise S3Error("PreconditionFailed")
        self.objects[Bucket, Key] = {"Body": Body, **extra}

    def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:
        if (Bucket, Key) not in self.objects:
            raise S3Error("NoSuchKey")
        return {"Body": io.BytesIO(self.objects[Bucket, Key]["Body"])}

    def head_object(self, Bucket: str, Key: str) -> dict[str, Any]:
        if (Bucket, Key) not in self.objects:
            raise S3Error("404")
        held = self.objects[Bucket, Key]
        return {
            "ContentLength": len(held["Body"]),
            "ContentType": held["ContentType"],
            "Metadata": held["Metadata"],
            "LastModified": NOON,
        }

    def delete_object(self, Bucket: str, Key: str) -> None:
        self.objects.pop((Bucket, Key), None)

    def list_objects_v2(self, Bucket: str, Prefix: str, **page: Any) -> dict[str, Any]:
        """One key per page, so the continuation is walked."""
        keys = sorted(
            key for bucket, key in self.objects if bucket == Bucket and key.startswith(Prefix)
        )
        start = int(page.get("ContinuationToken", 0))
        rest = keys[start:]
        return {
            "Contents": [{"Key": key} for key in rest[:1]],
            "IsTruncated": len(rest) > 1,
            "NextContinuationToken": str(start + 1),
        }

    def generate_presigned_url(self, operation: str, Params: dict, ExpiresIn: int) -> str:
        extra = "".join(
            f"&{name}={value}"
            for name, value in Params.items()
            if name not in ("Bucket", "Key")
        )
        return f"https://{Params['Bucket']}.s3/{Params['Key']}?op={operation}&expires={ExpiresIn}{extra}"


class TestInMemoryObjectStore(ObjectStoreContract):
    def make_store(self) -> ObjectStore:
        return InMemoryObjectStore()


class TestLocalObjectStore(ObjectStoreContract):
    @pytest.fixture(autouse=True)
    def _root(self, tmp_path: Path) -> None:
        self.root = tmp_path

    def make_store(self) -> ObjectStore:
        return LocalObjectStore(self.root / "objects")


class TestS3CompatibleObjectStore(ObjectStoreContract):
    def make_store(self) -> ObjectStore:
        return S3CompatibleObjectStore("documents", FakeS3(), prefix="billing/")


def test_a_bucket_keeps_every_key_under_its_prefix_and_answers_it_without():
    client = FakeS3()
    store = S3CompatibleObjectStore("documents", client, prefix="billing/")

    store.put("a.pdf", b"%PDF", "application/pdf", {}, False)

    assert ("documents", "billing/a.pdf") in client.objects
    assert list(store.keys("")) == ["a.pdf"]


def test_the_options_of_every_put_go_with_each_and_a_call_s_own_win():
    client = FakeS3()
    store = S3CompatibleObjectStore(
        "documents", client, put_options={"ServerSideEncryption": "AES256", "ACL": "private"}
    )

    store.put("a.pdf", b"%PDF", "application/pdf", {"by": "ana"}, False, ACL="public-read")

    assert client.objects["documents", "a.pdf"] == {
        "Body": b"%PDF",
        "ContentType": "application/pdf",
        "Metadata": {"by": "ana"},
        "ServerSideEncryption": "AES256",
        "ACL": "public-read",
    }


def test_a_bucket_presigns_with_the_parameters_it_is_given():
    store = S3CompatibleObjectStore("documents", FakeS3())

    read = store.presign(
        "a.pdf", Access.READ, timedelta(hours=1), ResponseContentDisposition="attachment"
    )
    write = store.presign("b.pdf", Access.WRITE, timedelta(minutes=5))

    assert read == (
        "https://documents.s3/a.pdf?op=get_object&expires=3600"
        "&ResponseContentDisposition=attachment"
    )
    assert write.endswith("op=put_object&expires=300")


def test_a_bucket_that_fails_is_an_external_service_error():
    client = FakeS3()
    client.down = True

    with pytest.raises(ExternalServiceError, match="failed to put a.pdf on documents"):
        S3CompatibleObjectStore("documents", client).put("a.pdf", b"%PDF", "x", {}, False)


def test_a_key_never_leaves_the_local_directory(tmp_path: Path):
    store = LocalObjectStore(tmp_path / "objects")

    with pytest.raises(ProgrammingError, match="leaves the store's directory"):
        store.put("../outside.txt", b"x", "text/plain", {}, False)
