"""`ObjectStoreContract`: the behaviour every `ObjectStore` owes, as a test class to inherit.

    class TestSpacesObjectStore(ObjectStoreContract):
        def make_store(self) -> ObjectStore:
            return S3CompatibleObjectStore("test-bucket", client)

A store of yours — another bucket, a database, a service — passes the same class, and then
`ObjectStorage` works on it. Nothing here imports pytest.
"""

import io

from sincpro_framework.data_layer.object_storage import ObjectStore


class ObjectStoreContract:
    """Inherit, answer `make_store` with a new, empty store, and run with pytest."""

    def make_store(self) -> ObjectStore:
        """Answer a new, empty store — objects from one test must not be seen by the next."""
        raise NotImplementedError("answer a new, empty store")

    def test_an_object_put_is_got_back(self) -> None:
        """What `put` stored is what `get` answers, byte for byte."""
        store = self.make_store()
        store.put("docs/a.pdf", b"%PDF-1.7", "application/pdf", {}, False)
        if store.get("docs/a.pdf") != b"%PDF-1.7":
            raise AssertionError("an object put was not got back")

    def test_a_file_like_put_is_got_back(self) -> None:
        """A file-like is read to its end and stored as its bytes."""
        store = self.make_store()
        store.put("big.bin", io.BytesIO(b"x" * 70_000), "application/octet-stream", {}, False)
        found = store.info("big.bin")
        if store.get("big.bin") != b"x" * 70_000 or found is None or found.size != 70_000:
            raise AssertionError("a file-like put was not got back")

    def test_metadata_of_any_text_is_read_as_written(self) -> None:
        """A value with accents, spaces and signs comes back as it went in."""
        store = self.make_store()
        said = {"filename": "facturación 2026 (copia) 100%.pdf"}
        store.put("a.pdf", b"%PDF", "application/pdf", said, False)
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
        store.put("a.csv", b"a,b\n1,2\n", "text/csv", {"filename": "a.csv"}, False)
        found = store.info("a.csv")
        said = (
            None
            if found is None
            else (found.key, found.size, found.content_type, found.metadata)
        )
        if said != ("a.csv", 8, "text/csv", {"filename": "a.csv"}):
            raise AssertionError(f"info answered {found!r}")

    def test_a_put_replaces_what_was_there(self) -> None:
        """A second `put` under the same key is what is got."""
        store = self.make_store()
        store.put("a.txt", b"one", "text/plain", {}, False)
        store.put("a.txt", b"two", "text/plain", {}, False)
        if store.get("a.txt") != b"two":
            raise AssertionError("a second put did not replace the first")

    def test_a_put_if_absent_never_replaces(self) -> None:
        """`if_absent` stores only under a key with nothing: `True` then, `False` and nothing
        written when an object is there."""
        store = self.make_store()
        first = store.put("claim", b"one", "text/plain", {}, True)
        second = store.put("claim", b"two", "text/plain", {}, True)
        if (first, second, store.get("claim")) != (True, False, b"one"):
            raise AssertionError("a put if_absent replaced what was there")

    def test_keys_answers_those_under_a_prefix(self) -> None:
        """`keys(prefix)` answers every key that starts with it, and `""` every key."""
        store = self.make_store()
        for key in ("inbox/a.pdf", "inbox/b.pdf", "exports/c.csv"):
            store.put(key, b"x", "application/octet-stream", {}, False)
        if sorted(store.keys("inbox/")) != ["inbox/a.pdf", "inbox/b.pdf"]:
            raise AssertionError(f"keys('inbox/') answered {sorted(store.keys('inbox/'))}")
        if len(list(store.keys(""))) != 3:
            raise AssertionError("keys('') did not answer every key")

    def test_a_deleted_object_is_gone_and_deleting_nothing_is_fine(self) -> None:
        """After `delete` nothing is under the key; deleting a missing key does not raise."""
        store = self.make_store()
        store.put("a.txt", b"x", "text/plain", {}, False)
        store.delete("a.txt")
        store.delete("never")
        if store.get("a.txt") is not None or list(store.keys("a.txt")):
            raise AssertionError("a deleted object was still there")
