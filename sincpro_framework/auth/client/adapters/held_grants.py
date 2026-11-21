"""Where the refresh tokens this service holds live — in this process, or on a `KeyValueStore`
every replica shares, sealed when given a `Sealer`.

    KeyValueHeldGrants(seal=AesGcmSealer(settings.grants_key))     the running context's store

Context: a held grant is written by compare-and-set: when two replicas refresh the same grant,
one stores its answer and the other keeps using the token it got, never overwriting the newer
refresh token with an older one — which a server rotating refresh tokens would take for reuse.
"""

import threading

from sincpro_framework.auth.client.domain.outbound import HeldGrant, HeldGrants, Sealer
from sincpro_framework.auth.infrastructure.digests import digest
from sincpro_framework.context.entrypoint.resolution import current_store, key_value_store_of
from sincpro_framework.data_layer.store import KeyValueStore


class InMemoryHeldGrants(HeldGrants):
    """Held grants in this process — a CLI, a test, one replica."""

    def __init__(self) -> None:
        self._held: dict[str, HeldGrant] = {}
        self._lock = threading.Lock()

    def current(self, key: str) -> HeldGrant | None:
        """The grant held under `key` — `None` when there is none."""
        with self._lock:
            return self._held.get(key)

    def replace(self, key: str, expected: HeldGrant | None, new: HeldGrant | None) -> bool:
        """Under one lock: `new` written — `None` drops it — only when `expected` is what is
        held."""
        with self._lock:
            if self._held.get(key) != expected:
                return False
            if new is None:
                self._held.pop(key, None)
            else:
                self._held[key] = new
            return True


class KeyValueHeldGrants(HeldGrants):
    """Held grants on a `KeyValueStore`, under digested keys, sealed when given a `Sealer`."""

    def __init__(
        self,
        store: KeyValueStore | None = None,
        seal: Sealer | None = None,
        prefix: str = "sincpro:auth:held",
    ) -> None:
        self._store = store
        self.seal = seal
        self.prefix = prefix

    def store(self) -> KeyValueStore:
        """The store the grants are kept in — the one given, else the running context's."""
        return self._store if self._store is not None else key_value_store_of(current_store())

    def _key(self, key: str) -> str:
        return f"{self.prefix}:{digest(key)}"

    def _encoded(self, grant: HeldGrant) -> bytes:
        plain = grant.model_dump_json().encode()
        return self.seal.seal(plain) if self.seal else plain

    def _decoded(self, raw: bytes) -> HeldGrant:
        plain = self.seal.open(raw) if self.seal else raw
        return HeldGrant.model_validate_json(plain)

    def current(self, key: str) -> HeldGrant | None:
        """The grant held under `key`, opened when it was sealed — `None` when there is none."""
        raw = self.store().get(self._key(key))
        return None if raw is None else self._decoded(raw)

    def replace(self, key: str, expected: HeldGrant | None, new: HeldGrant | None) -> bool:
        """Compared on what the grant is, not on its bytes — a sealed value differs every time
        it is sealed."""
        store = self.store()
        stored_key = self._key(key)
        raw = store.get(stored_key)
        held = None if raw is None else self._decoded(raw)
        if held != expected:
            return False
        if raw is None:
            return new is not None and store.add(stored_key, self._encoded(new))
        return store.replace(stored_key, raw, None if new is None else self._encoded(new))
