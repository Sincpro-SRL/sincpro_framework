"""`KeyValueStoreMixin`: what every auth component that stores on a `KeyValueStore` stands on —
the store given, else the running context's, resolved at every call, and a read-modify-write by
compare-and-set.

    class KeyValueClients(KeyValueStoreMixin, ClientRegistry):
        def __init__(self, store: KeyValueStore | None = None, prefix: str = "…") -> None:
            self._init_key_value_store(store, prefix)

        def find(self, client_id: str) -> OAuthClient | None:
            raw = self.store().get(f"{self.prefix}:{client_id}")
            ...

Context: a store resolved from the running context is the bus's (Redis across replicas) or this
process's memory. When it is this process's alone, a component that sets `warn_when_unshared`
says so once: with several replicas each one would count, lock or remember on its own. A store
given explicitly is never warned about — whoever passed it chose it.
"""

from collections.abc import Callable
from datetime import timedelta

from sincpro_framework.context.entrypoint.resolution import current_store, key_value_store_of
from sincpro_framework.data_layer.store import KeyValueStore
from sincpro_framework.exceptions import ServiceUnavailableError
from sincpro_framework.sincpro_logger import logger

MAX_COMPARE_AND_SET_ATTEMPTS = 64
"""How many times a contended read-modify-write is retried before answering 503."""


class KeyValueStoreMixin:
    """A store given or the running context's, a key prefix, and compare-and-set updates."""

    warn_when_unshared: bool = False
    """Whether a store resolved from the context that lives in this process alone is warned
    about once — set by components whose correctness needs every replica to see one store."""

    _store: KeyValueStore | None
    prefix: str
    _unshared_warned: bool

    def _init_key_value_store(self, store: KeyValueStore | None, prefix: str) -> None:
        """`store` pins one instead of the running context's; every key starts with `prefix`."""
        self._store = store
        self.prefix = prefix
        self._unshared_warned = False

    def store(self) -> KeyValueStore:
        """The store given, else the running context's — resolved at every call."""
        if self._store is not None:
            return self._store
        resolved = key_value_store_of(current_store())
        if self.warn_when_unshared and not self._unshared_warned and not resolved.shared():
            self._unshared_warned = True
            logger.warning(
                f"{type(self).__name__} stores in this process alone: with more than one "
                "replica give it a shared store (store=...) or run the bus on one"
            )
        return resolved

    def update(
        self,
        key: str,
        change: Callable[[bytes | None], tuple[bytes, timedelta | None]],
    ) -> None:
        """`change` applied to the value under `key` — `None` when there is none — and written
        with the expiry it answers, by compare-and-set: retried when another writer won,
        `ServiceUnavailableError` (503, try again) past `MAX_COMPARE_AND_SET_ATTEMPTS`."""
        store = self.store()
        for _ in range(MAX_COMPARE_AND_SET_ATTEMPTS):
            current = store.get(key)
            value, ttl = change(current)
            if current is None and store.add(key, value, ttl):
                return
            if current is not None and store.replace(key, current, value, ttl):
                return
        raise ServiceUnavailableError(f"{key} is contended by other writers: try again")
