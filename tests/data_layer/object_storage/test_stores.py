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
        self.asked: dict[str, dict[str, Any]] = {}
        """What each call was given beyond the bucket and the key."""

    def put_object(self, Bucket: str, Key: str, Body: Any, **extra: Any) -> None:
        """A file-like `Body` is read to its end, as the client streams it."""
        Body = Body if isinstance(Body, bytes) else Body.read()
        if self.down:
            raise S3Error("ServiceUnavailable")
        if extra.pop("IfNoneMatch", None) == "*" and (Bucket, Key) in self.objects:
            raise S3Error("PreconditionFailed")
        self.objects[Bucket, Key] = {"Body": Body, **extra}

    def upload_fileobj(
        self,
        Fileobj: Any,
        Bucket: str,
        Key: str,
        ExtraArgs: dict[str, Any] | None = None,
        Config: Any = None,
    ) -> None:
        """boto3's managed transfer: the file read by parts, its arguments as `put_object`'s."""
        self.asked["upload_fileobj"] = {"ExtraArgs": ExtraArgs, "Config": Config}
        parts = b"".join(iter(lambda: Fileobj.read(8 * 1024 * 1024), b""))
        self.put_object(Bucket, Key, parts, **(ExtraArgs or {}))

    def get_object(self, Bucket: str, Key: str, **extra: Any) -> dict[str, Any]:
        self.asked["get_object"] = extra
        if (Bucket, Key) not in self.objects:
            raise S3Error("NoSuchKey")
        held = self.objects[Bucket, Key]
        return {**self._head(Bucket, Key), "Body": io.BytesIO(held["Body"])}

    def _head(self, Bucket: str, Key: str) -> dict[str, Any]:
        held = self.objects[Bucket, Key]
        return {
            "ContentLength": len(held["Body"]),
            "ContentType": held["ContentType"],
            "Metadata": held["Metadata"],
            "LastModified": NOON,
        }

    def head_object(self, Bucket: str, Key: str, **extra: Any) -> dict[str, Any]:
        self.asked["head_object"] = extra
        if (Bucket, Key) not in self.objects:
            raise S3Error("404")
        return self._head(Bucket, Key)

    def delete_object(self, Bucket: str, Key: str, **extra: Any) -> None:
        self.asked["delete_object"] = extra
        self.objects.pop((Bucket, Key), None)

    def list_objects_v2(self, Bucket: str, Prefix: str, **page: Any) -> dict[str, Any]:
        """One key per page, so the continuation is walked."""
        self.asked["list_objects_v2"] = {
            name: value for name, value in page.items() if name != "ContinuationToken"
        }
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

    store.put("a.pdf", b"%PDF", "application/pdf", {}, False, None)

    assert ("documents", "billing/a.pdf") in client.objects
    assert list(store.keys("")) == ["a.pdf"]


def test_the_options_of_every_put_go_with_each_and_a_call_s_own_win():
    client = FakeS3()
    store = S3CompatibleObjectStore(
        "documents", client, put_options={"ServerSideEncryption": "AES256", "ACL": "private"}
    )

    store.put(
        "a.pdf", b"%PDF", "application/pdf", {"by": "ana"}, False, None, ACL="public-read"
    )

    assert client.objects["documents", "a.pdf"] == {
        "Body": b"%PDF",
        "ContentType": "application/pdf",
        "Metadata": {"by": "ana"},
        "ServerSideEncryption": "AES256",
        "ACL": "public-read",
    }


class RgwLikeClient(FakeS3):
    """A bucket that answers metadata the way DigitalOcean Spaces (Ceph RGW) does: the beast
    frontend turns a header into an environment variable (`-` → `_`), and `init_meta_info` turns
    it back into a name (`_` → `-`). `moto` keeps the `_` like AWS, so it never shows this."""

    def _head(self, Bucket: str, Key: str) -> dict[str, Any]:
        answered = super()._head(Bucket, Key)
        answered["Metadata"] = {
            name.replace("_", "-"): value for name, value in answered["Metadata"].items()
        }
        return answered


def test_what_a_bucket_like_spaces_answers_comes_back_as_written():
    store = S3CompatibleObjectStore("documents", RgwLikeClient())
    expires_at = datetime(2099, 1, 1, tzinfo=UTC)

    store.put(
        "a.pdf", b"%PDF", "application/pdf", {"file-name": "resumen.pdf"}, False, expires_at
    )

    found = store.get("a.pdf")
    assert found is not None
    assert (found.ref.metadata, found.ref.expires_at) == (
        {"file-name": "resumen.pdf"},
        expires_at,
    )


def test_a_bucket_answers_an_object_and_what_it_is_in_one_read():
    client = FakeS3()
    store = S3CompatibleObjectStore("documents", client)
    store.put("a.pdf", b"%PDF", "application/pdf", {"by": "ana"}, False, None)

    found = store.get("a.pdf")

    assert found is not None and (found.data, found.ref.metadata) == (b"%PDF", {"by": "ana"})
    assert "head_object" not in client.asked


class Unseekable(io.BufferedReader):
    """A file that can only be read forward — another object's stream, a request's body."""

    def __init__(self, data: bytes) -> None:
        super().__init__(io.BytesIO(data))

    def seekable(self) -> bool:
        return False


def test_a_file_is_put_by_parts_with_every_option_and_the_transfer_s_config():
    client = FakeS3()
    transfer = object()
    store = S3CompatibleObjectStore(
        "documents",
        client,
        put_options={"ServerSideEncryption": "AES256"},
        transfer_config=transfer,
    )

    store.put(
        "v.mp4",
        Unseekable(b"x" * 70_000),
        "video/mp4",
        {"by": "ana"},
        False,
        None,
        ACL="private",
    )

    assert client.asked["upload_fileobj"] == {
        "ExtraArgs": {
            "ContentType": "video/mp4",
            "Metadata": {"by": "ana"},
            "ServerSideEncryption": "AES256",
            "ACL": "private",
        },
        "Config": transfer,
    }
    assert client.objects["documents", "v.mp4"]["Body"] == b"x" * 70_000


def test_bytes_and_a_put_if_absent_are_one_put_object():
    client = FakeS3()
    store = S3CompatibleObjectStore("documents", client)

    store.put("a.pdf", b"%PDF", "application/pdf", {}, False, None)
    store.put("b.pdf", io.BytesIO(b"%PDF"), "application/pdf", {}, True, None)

    assert "upload_fileobj" not in client.asked
    assert {key for _, key in client.objects} == {"a.pdf", "b.pdf"}


def test_a_bucket_streams_the_response_s_body_unread():
    client = FakeS3()
    store = S3CompatibleObjectStore("documents", client)
    store.put("a.csv", b"a,b\n1,2\n", "text/csv", {"by": "ana"}, False, None)

    streamed = store.stream("a.csv")

    assert streamed is not None and streamed.ref.metadata == {"by": "ana"}
    assert (streamed.read(4), streamed.read()) == (b"a,b\n", b"1,2\n")


def test_a_bucket_carries_metadata_as_ascii_headers():
    client = FakeS3()
    store = S3CompatibleObjectStore("documents", client)

    store.put(
        "a.pdf", b"%PDF", "application/pdf", {"filename": "facturación.pdf"}, False, None
    )

    assert client.objects["documents", "a.pdf"]["Metadata"] == {
        "filename": "facturaci%C3%B3n.pdf"
    }


def test_every_call_hands_the_storage_its_own_options():
    client = FakeS3()
    store = S3CompatibleObjectStore("documents", client)
    sse_c = {"SSECustomerAlgorithm": "AES256", "SSECustomerKey": "k" * 32}
    store.put("a.pdf", b"%PDF", "application/pdf", {}, False, None, **sse_c)

    store.get("a.pdf", **sse_c)
    store.info("a.pdf", **sse_c)
    list(store.keys("", RequestPayer="requester"))
    store.delete("a.pdf", VersionId="v1")

    assert client.asked == {
        "get_object": sse_c,
        "head_object": sse_c,
        "list_objects_v2": {"RequestPayer": "requester"},
        "delete_object": {"VersionId": "v1"},
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
        S3CompatibleObjectStore("documents", client).put(
            "a.pdf", b"%PDF", "x", {}, False, None
        )


def test_a_key_never_leaves_the_local_directory(tmp_path: Path):
    store = LocalObjectStore(tmp_path / "objects")

    with pytest.raises(ProgrammingError, match="leaves the store's directory"):
        store.put("../outside.txt", b"x", "text/plain", {}, False, None)


def test_a_file_of_any_length_reaches_a_real_bucket_in_parts_and_streams_back():
    moto = pytest.importorskip("moto")
    boto3 = pytest.importorskip("boto3")
    transfer = pytest.importorskip("boto3.s3.transfer")
    five_mib = 5 * 1024 * 1024
    data = b"y" * (12 * 1024 * 1024)

    with moto.mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="documents")
        store = S3CompatibleObjectStore(
            "documents",
            client,
            prefix="billing/",
            transfer_config=transfer.TransferConfig(
                multipart_threshold=five_mib, multipart_chunksize=five_mib
            ),
        )

        store.put(
            "v.bin", Unseekable(data), "video/mp4", {"filename": "vídeo.bin"}, False, None
        )
        streamed = store.stream("v.bin")

        assert streamed is not None
        assert (streamed.ref.size, streamed.ref.metadata) == (
            len(data),
            {"filename": "vídeo.bin"},
        )
        assert b"".join(streamed.chunks()) == data
