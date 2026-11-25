"""`KeyValueOAuthClients` and `InMemoryOAuthClients`: the clients an authorization server knows —
on a `KeyValueStore` every replica shares (the running context's when none is given), or in this
process's memory. A registry of the project's own — its database, Odoo — implements
`ClientRegistry`."""

from collections.abc import Iterable

from sincpro_framework.auth.common.adapters.key_value import KeyValueStoreMixin
from sincpro_framework.auth.server.domain.authorization_server.clients import (
    ClientRegistry,
    OAuthClient,
)
from sincpro_framework.data_layer.store import InMemoryKeyValue, KeyValueStore


class KeyValueOAuthClients(KeyValueStoreMixin, ClientRegistry):
    """Clients on a `KeyValueStore`, one key each, stored until replaced."""

    warn_when_unshared = True

    def __init__(
        self, store: KeyValueStore | None = None, prefix: str = "sincpro:auth:oauth:client"
    ) -> None:
        self._init_key_value_store(store, prefix)

    def find(self, client_id: str) -> OAuthClient | None:
        raw = self.store().get(f"{self.prefix}:{client_id}")
        return None if raw is None else OAuthClient.model_validate_json(raw)

    def save(self, client: OAuthClient) -> None:
        self.store().set(
            f"{self.prefix}:{client.client_id}", client.model_dump_json().encode()
        )


class InMemoryOAuthClients(KeyValueOAuthClients):
    """Clients in this process — a test, one replica, a fixed list given at start."""

    def __init__(self, clients: Iterable[OAuthClient] = ()) -> None:
        super().__init__(InMemoryKeyValue())
        for client in clients:
            self.save(client)
