"""Where what a session keeps lives — on a `KeyValueStore` every replica shares
(`KeyValueSessionAttributes`), or in this process (`InMemorySessionAttributes`).

    KeyValueSessionAttributes()                       the running context's store
    KeyValueSessionAttributes(RedisKeyValue(...))     one given
    InMemorySessionAttributes()                       a test, a CLI, one replica

Context: keyed by the digest of the session id, never the id a token carries, as JSON — so a copy
of the store tells nobody which session holds what.
"""

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

from sincpro_framework.auth.accounts.domain.sessions import SessionAttributes
from sincpro_framework.auth.common.adapters.key_value import KeyValueStoreMixin
from sincpro_framework.auth.common.infrastructure.digests import digest
from sincpro_framework.data_layer.store import InMemoryKeyValue, KeyValueStore

MIN_TTL = timedelta(seconds=1)
"""The shortest anything is kept — what is saved always expires."""


class KeyValueSessionAttributes(KeyValueStoreMixin, SessionAttributes):
    """What sessions keep, on a `KeyValueStore`, one key per session."""

    warn_when_unshared = True

    def __init__(
        self, store: KeyValueStore | None = None, prefix: str = "sincpro:auth:session"
    ) -> None:
        """`store` pins one instead of the running context's."""
        self._init_key_value_store(store, prefix)

    def _key(self, session: str) -> str:
        return f"{self.prefix}:attributes:{digest(session)}"

    def save(self, session: str, values: Mapping[str, Any], ttl: timedelta) -> None:
        self.store().set(
            self._key(session),
            json.dumps(dict(values), sort_keys=True).encode(),
            max(ttl, MIN_TTL),
        )

    def find(self, session: str) -> dict[str, Any] | None:
        raw = self.store().get(self._key(session))
        return None if raw is None else json.loads(raw)

    def delete(self, session: str) -> None:
        self.store().delete(self._key(session))


class InMemorySessionAttributes(KeyValueSessionAttributes):
    """What sessions keep, in this process — a test, a CLI, one replica."""

    def __init__(self, now: Callable[[], datetime] = partial(datetime.now, UTC)) -> None:
        super().__init__(InMemoryKeyValue(now=now))
