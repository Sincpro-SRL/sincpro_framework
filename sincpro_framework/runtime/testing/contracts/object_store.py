"""`ObjectStoreContract`: the behaviour every `ObjectStore` owes, as a test class to inherit.

    class TestSpacesObjectStore(ObjectStoreContract):
        def make_store(self) -> ObjectStore:
            return S3CompatibleObjectStore("test-bucket", client)

A store of yours — another bucket, a database, a service — passes the same class, and then
`ObjectStorage` works on it. Nothing here imports pytest.
"""

import io
from datetime import UTC, datetime, timedelta

from sincpro_framework.data_layer.object_storage import ObjectStore


class ObjectStoreContract:
    """Inherit, answer `make_store` with a new, empty store, and run with pytest."""

    def make_store(self) -> ObjectStore:
        """Answer a new, empty store — objects from one test must not be seen by the next."""
        raise NotImplementedError("answer a new, empty store")

    def test_an_object_put_is_got_back(self) -> None:
        """What `put` stored is what `get` answers, byte for byte."""
        store = self.make_store()
        store.put("docs/a.pdf", b"%PDF-1.7", "application/pdf", {}, False, None)
        found = store.get("docs/a.pdf")
        if found is None or found.data != b"%PDF-1.7":
            raise AssertionError("an object put was not got back")

    def test_a_file_like_put_is_got_back(self) -> None:
        """A file-like is read to its end and stored as its bytes."""
        store = self.make_store()
        store.put(
            "big.bin", io.BytesIO(b"x" * 70_000), "application/octet-stream", {}, False, None
        )
        found = store.get("big.bin")
        if found is None or found.data != b"x" * 70_000 or found.ref.size != 70_000:
            raise AssertionError("a file-like put was not got back")

    def test_an_object_is_streamed_by_parts_with_what_it_is(self) -> None:
        """`stream` answers the bytes `put` stored, read part by part, and what `info` says of
        them; a missing key is `None`."""
        store = self.make_store()
        store.put(
            "big.bin", b"x" * 70_000, "application/octet-stream", {"by": "ana"}, False, None
        )
        streamed, said = store.stream("big.bin"), store.info("big.bin")
        if streamed is None or said is None:
            raise AssertionError("a stored object was not streamed")
        with streamed:
            parts = list(iter(lambda: streamed.read(16_384), b""))
        if b"".join(parts) != b"x" * 70_000 or max(len(part) for part in parts) > 16_384:
            raise AssertionError("a stream did not answer the bytes part by part")
        if (streamed.ref.size, streamed.ref.metadata) != (said.size, said.metadata):
            raise AssertionError(f"stream answered {streamed.ref!r}, info {said!r}")
        if store.stream("never") is not None:
            raise AssertionError("a missing key was streamed")

    def test_metadata_of_any_text_is_read_as_written(self) -> None:
        """A value with accents, spaces and signs comes back as it went in."""
        store = self.make_store()
        said = {"filename": "facturación 2026 (copia) 100%.pdf"}
        store.put("a.pdf", b"%PDF", "application/pdf", said, False, None)
        found = store.info("a.pdf")
        if found is None or found.metadata != said:
            raise AssertionError(f"metadata came back as {found and found.metadata!r}")

    def test_a_missing_key_is_none(self) -> None:
        """Nothing under a key: `get` and `info` answer `None`."""
        store = self.make_store()
        if store.get("never") is not None or store.info("never") is not None:
            raise AssertionError("a missing key answered something")

    def test_info_says_what_was_stored_without_its_bytes(self) -> None:
        """`info` answers the key, size, content type and metadata `put` was given."""
        store = self.make_store()
        store.put("a.csv", b"a,b\n1,2\n", "text/csv", {"filename": "a.csv"}, False, None)
        found = store.info("a.csv")
        said = (
            None
            if found is None
            else (found.key, found.size, found.content_type, found.metadata)
        )
        if said != ("a.csv", 8, "text/csv", {"filename": "a.csv"}):
            raise AssertionError(f"info answered {found!r}")

    def test_get_answers_what_info_does_with_the_bytes(self) -> None:
        """`get` reads the object and what it is at once: the same key, size, type, metadata
        and expiry `info` answers."""
        store = self.make_store()
        expires_at = datetime(2099, 1, 1, tzinfo=UTC)
        store.put("a.csv", b"a,b\n", "text/csv", {"filename": "a.csv"}, False, expires_at)
        found, said = store.get("a.csv"), store.info("a.csv")
        if found is None or said is None:
            raise AssertionError("a stored object was not found")
        got = found.ref
        if (got.key, got.size, got.content_type, got.metadata, got.expires_at) != (
            said.key,
            said.size,
            said.content_type,
            said.metadata,
            said.expires_at,
        ):
            raise AssertionError(f"get answered {got!r}, info {said!r}")

    def test_an_expiry_is_answered_back_as_given(self) -> None:
        """`expires_at` is kept and answered back — judging it is the services', not the store's."""
        store = self.make_store()
        expires_at = datetime.now(UTC).replace(microsecond=0) + timedelta(hours=1)
        store.put("a.txt", b"x", "text/plain", {}, False, expires_at)
        found = store.info("a.txt")
        if found is None or found.expires_at != expires_at:
            raise AssertionError(f"the expiry came back as {found and found.expires_at!r}")

    def test_a_put_replaces_what_was_there(self) -> None:
        """A second `put` under the same key is what is got."""
        store = self.make_store()
        store.put("a.txt", b"one", "text/plain", {}, False, None)
        store.put("a.txt", b"two", "text/plain", {}, False, None)
        found = store.get("a.txt")
        if found is None or found.data != b"two":
            raise AssertionError("a second put did not replace the first")

    def test_a_put_if_absent_never_replaces(self) -> None:
        """`if_absent` stores only under a key with nothing: `True` then, `False` and nothing
        written when an object is there."""
        store = self.make_store()
        first = store.put("claim", b"one", "text/plain", {}, True, None)
        second = store.put("claim", b"two", "text/plain", {}, True, None)
        found = store.get("claim")
        if (first, second, found and found.data) != (True, False, b"one"):
            raise AssertionError("a put if_absent replaced what was there")

    def test_keys_answers_those_under_a_prefix(self) -> None:
        """`keys(prefix)` answers every key that starts with it, and `""` every key."""
        store = self.make_store()
        for key in ("inbox/a.pdf", "inbox/b.pdf", "exports/c.csv"):
            store.put(key, b"x", "application/octet-stream", {}, False, None)
        if sorted(store.keys("inbox/")) != ["inbox/a.pdf", "inbox/b.pdf"]:
            raise AssertionError(f"keys('inbox/') answered {sorted(store.keys('inbox/'))}")
        if len(list(store.keys(""))) != 3:
            raise AssertionError("keys('') did not answer every key")

    def test_a_deleted_object_is_gone_and_deleting_nothing_is_fine(self) -> None:
        """After `delete` nothing is under the key; deleting a missing key does not raise."""
        store = self.make_store()
        store.put("a.txt", b"x", "text/plain", {}, False, None)
        store.delete("a.txt")
        store.delete("never")
        if store.get("a.txt") is not None or list(store.keys("a.txt")):
            raise AssertionError("a deleted object was still there")
