"""Where the OAuth grants this service holds live — in this process, or on a `KeyValueStore` every
replica shares, encrypted when given a `Sealer`.

    KeyValueOAuthGrants(sealer=AesGcmSealer(settings.grants_key))     the running context's store
    InMemoryOAuthGrants()                                             a CLI, a test, one replica

Context: a grant is written by compare-and-set on its `version`: when two replicas refresh the
same grant, one stores its answer and the other uses the token it got, never overwriting the newer
refresh token with an older one — which a server rotating refresh tokens would take for reuse. A
store resolved from the context that lives in this process alone is warned about once: with
several replicas each would rotate on its own.
"""

import threading

from sincpro_framework.auth.client.domain.outbound import OAuthGrant, OAuthGrantStore, Sealer
from sincpro_framework.auth.client.infrastructure.sealed_codec import SealedJsonCodec
from sincpro_framework.auth.common.adapters.key_value import KeyValueStoreMixin
from sincpro_framework.auth.common.infrastructure.digests import digest
from sincpro_framework.data_layer.store import KeyValueStore


class InMemoryOAuthGrants(OAuthGrantStore):
    """OAuth grants in this process — a CLI, a test, one replica."""

    def __init__(self) -> None:
        self._grants: dict[str, OAuthGrant] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> OAuthGrant | None:
        """The grant stored under `key` — `None` when there is none."""
        with self._lock:
            return self._grants.get(key)

    def compare_and_set(
        self, key: str, expected_version: int | None, grant: OAuthGrant | None
    ) -> bool:
        """Under one lock: `grant` written — `None` deletes it — only when the stored grant has
        `expected_version`."""
        with self._lock:
            stored = self._grants.get(key)
            if (None if stored is None else stored.version) != expected_version:
                return False
            if grant is None:
                self._grants.pop(key, None)
            else:
                self._grants[key] = grant
            return True


class KeyValueOAuthGrants(KeyValueStoreMixin, OAuthGrantStore):
    """OAuth grants on a `KeyValueStore`, under digested keys, encrypted when given a `Sealer`."""

    warn_when_unshared = True

    def __init__(
        self,
        store: KeyValueStore | None = None,
        sealer: Sealer | None = None,
        prefix: str = "sincpro:auth:oauth-grants",
    ) -> None:
        self._init_key_value_store(store, prefix)
        self._codec = SealedJsonCodec(OAuthGrant, "oauth-grant-1", sealer)

    def get(self, key: str) -> OAuthGrant | None:
        """The grant stored under `key`, decrypted when it was encrypted — `None` when there is
        none."""
        raw = self.store().get(f"{self.prefix}:{digest(key)}")
        return None if raw is None else self._codec.decode(raw)

    def compare_and_set(
        self, key: str, expected_version: int | None, grant: OAuthGrant | None
    ) -> bool:
        """Compared on the stored grant's version, then written by the store's own
        compare-and-set on the bytes just read — an encrypted value differs every time it is
        encrypted, so the bytes are never what is compared against `expected_version`."""
        store = self.store()
        stored_key = f"{self.prefix}:{digest(key)}"
        raw = store.get(stored_key)
        stored = None if raw is None else self._codec.decode(raw)
        if (None if stored is None else stored.version) != expected_version:
            return False
        if raw is None:
            return grant is not None and store.add(stored_key, self._codec.encode(grant))
        return store.replace(
            stored_key, raw, None if grant is None else self._codec.encode(grant)
        )
