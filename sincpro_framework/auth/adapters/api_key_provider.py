"""`ApiKeyProvider`: a key per script, integration or bot, each bound to who it stands for.

    keys = InMemoryApiKeys()
    secret = keys.issue(ApiKey(subject="apikey:ops-bot", tenant="bo", permissions={...}))
    auth = AccessControl(providers=[ApiKeyProvider(keys)])

Context: a key is stored by its SHA-256, never as issued — a store that leaks leaks no key; the
secret is shown once, when issued. The key is read from `header` (`x-api-key` by default), from
`Authorization: ApiKey <key>`, and from the query parameter `query` when one is named — the
last for a legacy client that can send nothing else. `ApiKeyStore` is the port: a project stores
its keys in its own table by implementing two methods.
"""

import hashlib
import secrets
from abc import ABC, abstractmethod
from typing import Any

from pydantic import ConfigDict

from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.domain.identity import Credentials, Identity, IdentityKind
from sincpro_framework.auth.domain.provider import AuthProvider
from sincpro_framework.sincpro_abstractions import DataTransferObject


def digest_of(key: str) -> str:
    """The SHA-256 of `key`, in hex — what a store saves and looks a key up by.

    Context: an API key is a long random secret, so a plain hash is enough — no salt, no
    stretching — and it lets the store find the key in one lookup instead of comparing each.

        digest_of("sk_3f9…")   ->  "a1b2…" (64 hex characters)
    """
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
    """The port where the keys live — `find` and `save` by digest; issuing and revoking come
    with it.

    Context: a project stores its keys in its own table by implementing the two abstract
    methods; `issue` and `revoke` are written once here, so no store can save a secret by
    mistake.
    """

    @abstractmethod
    def find(self, digest: str) -> ApiKey | None:
        """The key stored under `digest`, revoked or not; `None` when none is."""

    @abstractmethod
    def save(self, key: ApiKey) -> None:
        """Store `key` under its `digest`, replacing what was there."""

    def issue(self, key: ApiKey) -> str:
        """A new secret for `key`, stored by its digest — the only time the secret is seen."""
        secret = f"sk_{secrets.token_urlsafe(32)}"
        self.save(key.model_copy(update={"digest": digest_of(secret)}))
        return secret

    def revoke(self, secret_or_digest: str) -> None:
        """Mark a key revoked — by its secret, or by its digest when the secret is gone.

        Context: an operator rarely holds the secret after it was handed on; the digest, which
        the store shows, is enough. The key stays stored, flagged, so a call with it is told
        "revoked api key" instead of "unknown api key".

            keys.revoke("sk_3f9…")        # by the secret
            keys.revoke("a1b2…")          # by its digest (64 characters)

        1. A 64-character value is taken as a digest; anything else is hashed first.
        2. The key under that digest is looked up — an unknown one is left alone, no error.
        Final: the key is saved again with `revoked=True`.
        """
        digest = (
            secret_or_digest if len(secret_or_digest) == 64 else digest_of(secret_or_digest)
        )
        found = self.find(digest)
        if found is not None:
            self.save(found.model_copy(update={"revoked": True}))


class InMemoryApiKeys(ApiKeyStore):
    """An `ApiKeyStore` in a dict — for tests, and for a process issuing its keys at start.

    Context: it forgets every key when the process ends; production stores them in a table.
    """

    def __init__(self) -> None:
        """An empty store."""
        self._by_digest: dict[str, ApiKey] = {}

    def find(self, digest: str) -> ApiKey | None:
        """The key under `digest` from the dict."""
        return self._by_digest.get(digest)

    def save(self, key: ApiKey) -> None:
        """Put `key` in the dict under its digest."""
        self._by_digest[key.digest] = key


class ApiKeyProvider(AuthProvider):
    """An `AuthProvider` that vouches for a caller presenting an API key from `store`.

    Context: what an integration, a script or a bot uses — no login, no expiry, revoked by
    hand. Every key it accepts is a SERVICE identity carrying the subject, tenant and
    permissions the key was issued with; the provider resolves nothing else.
    """

    def __init__(
        self,
        store: ApiKeyStore,
        header: str = "x-api-key",
        query: str | None = None,
        name: str = "api-keys",
    ) -> None:
        """A provider over `store`, reading the key from `header` and, when named, from the
        query parameter `query` — the header name is lower-cased, as `Credentials` stores them.
        """
        self.store = store
        self.header = header.lower()
        self.query = query
        self.name = name

    def security_scheme(self) -> dict[str, Any] | None:
        """An `apiKey` in `header` — the query parameter, a legacy fallback, is not shown."""
        return {"type": "apiKey", "in": "header", "name": self.header}

    def _key_in(self, credentials: Credentials) -> str | None:
        """The key the caller sent, wherever it sent it; `None` when it sent none.

            Authorization: ApiKey sk_3f9…   ->  "sk_3f9…"
            x-api-key: sk_3f9…              ->  "sk_3f9…"
            ?api_key=sk_3f9… (query named)  ->  "sk_3f9…"

        1. `Authorization: ApiKey <key>` wins when present and not blank.
        2. Else the configured `header`.
        Final: else the query parameter, only when one was named.
        """
        scheme, _, value = credentials.headers.get("authorization", "").partition(" ")
        if scheme.lower() == "apikey" and value.strip():
            return value.strip()
        if self.header in credentials.headers:
            return credentials.headers[self.header]
        return credentials.query.get(self.query) if self.query else None

    def authenticate(self, credentials: Credentials) -> Identity | None:
        """The SERVICE identity a known, live key was issued for.

            x-api-key: <secret issued for ops-bot>
                ->  Identity(subject="apikey:ops-bot", kind=SERVICE, provider="api-keys")
            x-api-key: sk_nope        ->  raises Unauthenticated("unknown api key")
            no key at all             ->  None

        1. No key in the credentials: `None` — not this provider's kind, the next is tried.
        2. A key whose digest the store does not know: `Unauthenticated("unknown api key")`.
        3. A revoked key: `Unauthenticated("revoked api key")`.
        Final: an `Identity` with the key's subject, tenant and permissions, `provider=name`.
        """
        key = self._key_in(credentials)
        if key is None:
            return None
        stored = self.store.find(digest_of(key))
        if stored is None:
            raise exceptions.Unauthenticated("unknown api key")
        if stored.revoked:
            raise exceptions.Unauthenticated("revoked api key")
        return Identity(
            subject=stored.subject,
            kind=IdentityKind.SERVICE,
            tenant=stored.tenant,
            permissions=stored.permissions,
            provider=self.name,
        )
