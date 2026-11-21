"""`ApiKeyStoreContract`: what every `ApiKeyStore` must do — a project keeping its keys in its own
table proves it by inheriting this class.

    class TestMyKeys(ApiKeyStoreContract):
        def make_store(self) -> ApiKeyStore:
            return MyKeys(...)        new and empty for each test
"""

from datetime import UTC, datetime, timedelta

from sincpro_framework.auth.server import ApiKey, ApiKeyStore, digest_of


class ApiKeyStoreContract:
    """Inherit, answer `make_store` with a new, empty store, and run with pytest."""

    def make_store(self) -> ApiKeyStore:
        """Answer a new, empty store."""
        raise NotImplementedError("answer a new, empty ApiKeyStore")

    def test_an_issued_key_is_found_by_its_secret_and_never_kept_as_issued(self) -> None:
        """An issued key is found by its secret's digest with what it grants, and its digest
        is never the secret."""
        store = self.make_store()
        expires = datetime.now(UTC).replace(microsecond=0) + timedelta(days=30)
        secret = store.issue(
            ApiKey(
                subject="apikey:bot",
                tenant="bo",
                permissions=frozenset({"a.b.c"}),
                scopes=frozenset({"read"}),
                expires_at=expires,
            )
        )
        found = store.find(digest_of(secret))
        assert found is not None and found.digest != secret
        assert (found.subject, found.tenant, found.permissions, found.scopes) == (
            "apikey:bot",
            "bo",
            {"a.b.c"},
            {"read"},
        )
        assert found.expires_at == expires
        assert store.find(digest_of("sk_unknown")) is None

    def test_a_revoked_key_is_kept_flagged(self) -> None:
        """A revoked key is still found — flagged, so the provider refuses it."""
        store = self.make_store()
        secret = store.issue(ApiKey(subject="apikey:bot"))
        store.revoke(secret)
        found = store.find(digest_of(secret))
        assert found is not None and found.revoked
