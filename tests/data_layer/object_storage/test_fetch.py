"""`Transfers.fetch`: a URL somebody gave, read into the store a part at a time — refused past its
size, its time, a redirect not followed, a host it does not take — and nothing of it kept when it
is refused."""

import threading
import time
from collections.abc import Iterator
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from sincpro_framework.data_layer.object_storage import (
    FetchFailed,
    FetchRefusal,
    FetchRefused,
    InMemoryObjectStore,
    LocalObjectStore,
    S3CompatibleObjectStore,
)
from sincpro_framework.data_layer.object_storage.services.objects import Objects
from sincpro_framework.data_layer.object_storage.services.transfers import Transfers
from tests.data_layer.object_storage.test_stores import FakeS3

SCAN = b"%PDF-1.7\n" + b"x" * 200_000
QUICK = timedelta(milliseconds=300)


class Origin(BaseHTTPRequestHandler):
    """A server a user's URL points at: a document, one with no length, a slow one, redirects,
    a client error and a server error."""

    def log_message(self, format: str, *args: Any) -> None:
        return None

    def do_GET(self) -> None:
        port = self.headers["Host"].rsplit(":", 1)[1]
        if self.path == "/scan.pdf":
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(SCAN)))
            self.end_headers()
            self.wfile.write(SCAN)
        elif self.path == "/endless":  # no Content-Length: only counting stops it
            self.send_response(200)
            self.end_headers()
            for _ in range(200):
                self.wfile.write(b"y" * 64 * 1024)
        elif self.path == "/slow":
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            for _ in range(100):
                self.wfile.write(b"z")
                self.wfile.flush()
                time.sleep(0.05)
        elif self.path == "/moved":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{port}/scan.pdf")
            self.end_headers()
        elif self.path == "/moved-away":
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{port}/scan.pdf")
            self.end_headers()
        elif self.path == "/broken":
            self.send_response(500)
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture(scope="module")
def origin() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Origin)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_a_url_s_bytes_reach_the_store_with_what_its_server_says(origin: str, tmp_path: Path):
    store = LocalObjectStore(tmp_path / "objects")

    stored = Transfers(store).fetch(
        f"{origin}/scan.pdf", "inbox/scan.pdf", metadata={"filename": "scan.pdf"}
    )

    assert (stored.size, stored.content_type, stored.metadata) == (
        len(SCAN),
        "application/pdf",
        {"filename": "scan.pdf"},
    )
    assert (tmp_path / "objects" / "inbox" / "scan.pdf").read_bytes() == SCAN


def test_the_same_url_reaches_a_bucket_and_a_disk_with_the_same_size(
    origin: str, tmp_path: Path
):
    bucket = S3CompatibleObjectStore("documents", FakeS3())
    disk = LocalObjectStore(tmp_path / "objects")

    into_bucket = Transfers(bucket).fetch(f"{origin}/scan.pdf", "a.pdf")
    into_disk = Transfers(disk).fetch(f"{origin}/scan.pdf", "a.pdf", "application/x-scan")

    assert into_bucket.size == into_disk.size == len(SCAN)
    assert into_disk.content_type == "application/x-scan"  # the one given wins


def test_a_declared_length_past_the_cap_is_refused_before_a_byte_is_read(origin: str):
    store = InMemoryObjectStore()

    with pytest.raises(FetchRefused) as refused:
        Transfers(store).fetch(f"{origin}/scan.pdf", "a.pdf", max_bytes=1024)

    assert refused.value.reason is FetchRefusal.TOO_LARGE
    assert store.info("a.pdf") is None


@pytest.mark.parametrize("kind", ["memory", "disk", "bucket"])
def test_bytes_past_the_cap_are_refused_as_they_come_and_nothing_is_kept(
    origin: str, tmp_path: Path, kind: str
):
    store = {
        "memory": InMemoryObjectStore(),
        "disk": LocalObjectStore(tmp_path / "objects"),
        "bucket": S3CompatibleObjectStore("documents", FakeS3()),
    }[kind]

    with pytest.raises(FetchRefused) as refused:
        Transfers(store).fetch(f"{origin}/endless", "a.bin", max_bytes=256 * 1024)

    assert refused.value.reason is FetchRefusal.TOO_LARGE  # a bucket's wrapping seen through
    assert store.info("a.bin") is None and list(store.keys("")) == []


def test_a_refused_fetch_leaves_what_was_already_under_the_key(origin: str, tmp_path: Path):
    store = LocalObjectStore(tmp_path / "objects")
    Objects(store).put("a.bin", b"kept")

    with pytest.raises(FetchRefused):
        Transfers(store).fetch(f"{origin}/endless", "a.bin", max_bytes=256 * 1024)

    found = store.get("a.bin")
    assert found is not None and found.data == b"kept"


def test_a_url_slower_than_its_time_fails_and_nothing_is_kept(origin: str):
    store = InMemoryObjectStore()

    started = time.monotonic()
    with pytest.raises(FetchFailed):
        Transfers(store).fetch(f"{origin}/slow", "a.bin", timeout=QUICK)

    assert time.monotonic() - started < 3 * QUICK.total_seconds()
    assert store.info("a.bin") is None


def test_a_redirect_is_refused_unless_followed(origin: str):
    transfers = Transfers(InMemoryObjectStore())

    with pytest.raises(FetchRefused) as refused:
        transfers.fetch(f"{origin}/moved", "a.pdf")
    followed = transfers.fetch(f"{origin}/moved", "a.pdf", follow_redirects=True)

    assert (refused.value.reason, refused.value.status) == (FetchRefusal.REDIRECTED, 302)
    assert followed.size == len(SCAN)


def test_the_host_rule_judges_the_url_and_every_redirect(origin: str):
    transfers = Transfers(InMemoryObjectStore())

    allowed = transfers.fetch(f"{origin}/scan.pdf", "a.pdf", allowed_hosts={"127.0.0.1"})
    with pytest.raises(FetchRefused) as elsewhere:
        transfers.fetch(f"{origin}/scan.pdf", "b.pdf", allowed_hosts={"cdn.acme.bo"})
    with pytest.raises(FetchRefused) as redirected_away:
        transfers.fetch(
            f"{origin}/moved-away",
            "c.pdf",
            follow_redirects=True,
            allowed_hosts={"127.0.0.1"},
        )

    assert allowed.size == len(SCAN)
    assert elsewhere.value.reason is FetchRefusal.HOST_NOT_ALLOWED
    assert redirected_away.value.reason is FetchRefusal.HOST_NOT_ALLOWED
    assert redirected_away.value.url.startswith("http://localhost:")


def test_any_scheme_the_standard_library_opens_is_fetched_unless_told_otherwise(tmp_path):
    local = tmp_path / "scan.pdf"
    local.write_bytes(SCAN)
    transfers = Transfers(InMemoryObjectStore())

    fetched = transfers.fetch(local.as_uri(), "inbox/scan.pdf")
    with pytest.raises(FetchRefused) as refused:
        transfers.fetch(local.as_uri(), "inbox/again.pdf", schemes={"https"})

    assert fetched.size == len(SCAN)
    assert refused.value.reason is FetchRefusal.SCHEME_NOT_ALLOWED


def test_a_client_error_is_refused_and_a_server_error_fails(origin: str):
    transfers = Transfers(InMemoryObjectStore())

    with pytest.raises(FetchRefused) as missing:
        transfers.fetch(f"{origin}/missing", "a.pdf")
    with pytest.raises(FetchFailed) as broken:
        transfers.fetch(f"{origin}/broken", "a.pdf")
    with pytest.raises(FetchFailed):
        transfers.fetch("http://127.0.0.1:9/never", "a.pdf", timeout=QUICK)

    assert (missing.value.reason, missing.value.status) == (FetchRefusal.ANSWERED, 404)
    assert broken.value.status == 500
