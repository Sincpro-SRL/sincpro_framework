"""`ClientKeyValue`: what the engines that reach a server through a client share — Redis, Valkey,
Memcached, DynamoDB.

    class RedisKeyValue(ClientKeyValue):
        def get_many(self, keys):
            with self._reaching():               a client error is `StoreUnavailable`
                return self.client.mget(keys)

Context: every client raises its own errors — a socket timeout, a refused connection, a botocore
error. A caller of any store sees one: `StoreUnavailable`, the client's error as its cause.
"""

from collections.abc import Generator
from contextlib import contextmanager

from sincpro_framework.data_layer.store.domain import exceptions
from sincpro_framework.data_layer.store.domain.key_value import KeyValueStore


class ClientKeyValue(KeyValueStore):
    """A `KeyValueStore` on a client of its own: whatever the client raises is the store not
    answering."""

    @contextmanager
    def _reaching(self) -> Generator[None, None, None]:
        """A call to the store's own client: whatever it raises is the store not answering —
        `StoreUnavailable`, carrying the client's error as its cause."""
        try:
            yield
        except exceptions.StoreUnavailable:
            raise
        except Exception as error:
            raise exceptions.StoreUnavailable(
                f"{type(self).__name__} did not answer: {type(error).__name__}: {error}"
            ) from error
