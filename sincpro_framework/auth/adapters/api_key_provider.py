"""`ApiKeyProvider`: a key per script, integration or bot, each bound to who it stands for.

    keys = InMemoryApiKeys()
    secret = keys.issue(ApiKey(subject="apikey:ops-bot", tenant="bo", permissions={...}))
    auth = AccessControl(providers=[ApiKeyProvider(keys)])

Context: a key is kept by its SHA-256, never as issued — a store that leaks leaks no key; the
secret is shown once, when issued. The key is read from `header` (`x-api-key` by default), from
`Authorization: ApiKey <key>`, and from the query parameter `query` when one is named — the
last for a legacy client that can send nothing else. `ApiKeyStore` is the port: a project keeps
its keys in its own table by implementing two methods.
"""

import hashlib
import secrets
from abc import ABC, abstractmethod
from typing import Any

from pydantic import ConfigDict

from sincpro_framework.auth.domain.exceptions import Unauthenticated
from sincpro_framework.auth.domain.identity import Credentials, Identity, IdentityKind
from sincpro_framework.auth.domain.provider import AuthProvider
from sincpro_framework.sincpro_abstractions import DataTransferObject


def digest_of(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class ApiKey(DataTransferObject):
    """Who a key stands for — never the key itself, which only its `digest` remembers."""

    model_config = ConfigDict(frozen=True)

    subject: str
    tenant: str | None = None
    permissions: frozenset[str] = frozenset()
    digest: str = ""
    revoked: bool = False


class ApiKeyStore(ABC):
    @abstractmethod
    def find(self, digest: str) -> ApiKey | None:
        """The key kept under `digest`, revoked or not; `None` when none is."""

    @abstractmethod
    def keep(self, key: ApiKey) -> None:
        """Keep `key` under its `digest`, replacing what was there."""

    def issue(self, key: ApiKey) -> str:
        """A new secret for `key`, kept by its digest — the only time the secret is seen."""
        secret = f"sk_{secrets.token_urlsafe(32)}"
        self.keep(key.model_copy(update={"digest": digest_of(secret)}))
        return secret

    def revoke(self, secret_or_digest: str) -> None:
        digest = (
            secret_or_digest if len(secret_or_digest) == 64 else digest_of(secret_or_digest)
        )
        found = self.find(digest)
        if found is not None:
            self.keep(found.model_copy(update={"revoked": True}))


class InMemoryApiKeys(ApiKeyStore):
    def __init__(self) -> None:
        self._by_digest: dict[str, ApiKey] = {}

    def find(self, digest: str) -> ApiKey | None:
        return self._by_digest.get(digest)

    def keep(self, key: ApiKey) -> None:
        self._by_digest[key.digest] = key


class ApiKeyProvider(AuthProvider):
    def __init__(
        self,
        store: ApiKeyStore,
        header: str = "x-api-key",
        query: str | None = None,
        name: str = "api-keys",
    ) -> None:
        self.store = store
        self.header = header.lower()
        self.query = query
        self.name = name

    def security_scheme(self) -> dict[str, Any] | None:
        return {"type": "apiKey", "in": "header", "name": self.header}

    def _key_in(self, credentials: Credentials) -> str | None:
        scheme, _, value = credentials.headers.get("authorization", "").partition(" ")
        if scheme.lower() == "apikey" and value.strip():
            return value.strip()
        if self.header in credentials.headers:
            return credentials.headers[self.header]
        return credentials.query.get(self.query) if self.query else None

    def authenticate(self, credentials: Credentials) -> Identity | None:
        key = self._key_in(credentials)
        if key is None:
            return None
        kept = self.store.find(digest_of(key))
        if kept is None:
            raise Unauthenticated("unknown api key")
        if kept.revoked:
            raise Unauthenticated("revoked api key")
        return Identity(
            subject=kept.subject,
            kind=IdentityKind.SERVICE,
            tenant=kept.tenant,
            permissions=kept.permissions,
            provider=self.name,
        )
