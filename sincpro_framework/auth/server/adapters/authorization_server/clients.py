"""`InMemoryClients` and `KeyValueClients`: the clients an authorization server knows — in this
process, or on a `KeyValueStore` every replica shares (the running context's when none is given).
A registry of the project's own — its database, Odoo — implements `ClientRegistry`."""

import threading

from sincpro_framework.auth.server.domain.authorization_server.clients import (
    ClientRegistry,
    OAuthClient,
)
from sincpro_framework.context.entrypoint.resolution import current_store, key_value_store_of
from sincpro_framework.data_layer.store import KeyValueStore


class InMemoryClients(ClientRegistry):
    """Clients in this process — a test, one replica, a fixed list given at start."""

    def __init__(self, clients: tuple[OAuthClient, ...] = ()) -> None:
        self._clients = {one.client_id: one for one in clients}
        self._lock = threading.Lock()

    def find(self, client_id: str) -> OAuthClient | None:
        with self._lock:
            return self._clients.get(client_id)

    def save(self, client: OAuthClient) -> None:
        with self._lock:
            self._clients[client.client_id] = client


class KeyValueClients(ClientRegistry):
    """Clients on a `KeyValueStore`, one key each, living until removed."""

    def __init__(
        self, store: KeyValueStore | None = None, prefix: str = "sincpro:auth:oauth:client"
    ) -> None:
        self._store = store
        self.prefix = prefix

    def store(self) -> KeyValueStore:
        """The store given, else the running context's."""
        return self._store if self._store is not None else key_value_store_of(current_store())

    def find(self, client_id: str) -> OAuthClient | None:
        raw = self.store().get(f"{self.prefix}:{client_id}")
        return None if raw is None else OAuthClient.model_validate_json(raw)

    def save(self, client: OAuthClient) -> None:
        self.store().set(
            f"{self.prefix}:{client.client_id}", client.model_dump_json().encode()
        )
