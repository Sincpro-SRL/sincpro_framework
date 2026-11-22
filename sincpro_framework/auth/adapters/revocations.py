"""`KeyValueRevocations`: `Revocations` on a `KeyValueStore` — the context's by default, Redis in
production, `DatabaseKeyValue` for a project that keeps it in its database.

    revocations = KeyValueRevocations()                       the running context's store
    revocations = KeyValueRevocations(RedisKeyValue.from_url(url, prefix="billing"))
    InMemoryRevocations()                                     one process: tests, a CLI

Context: every key is a digest — `sincpro:auth:revoked:sid:<sha256>` — so neither a session id
nor a subject (an email, often) lands in the store as written. `read` is one `get_many`. What a
store that does not answer does is a choice the project makes: `"refuse"` (the default) lets
`StoreUnavailable` through — every call answers 503 until it is back, nothing revoked is ever
accepted —, `"admit"` treats the store as saying nothing was revoked, logs a warning and leaves
it alone for a while (`StoreBypass`), so a Redis restart does not take the service down.
"""

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import partial

from sincpro_framework.auth.domain.revocations import (
    Revocations,
    RevocationState,
    WhenUnavailable,
)
from sincpro_framework.auth.infrastructure.digests import digest
from sincpro_framework.context.entrypoint.resolution import current_store, key_value_store_of
from sincpro_framework.data_layer.store import (
    InMemoryKeyValue,
    KeyValueStore,
    StoreBypass,
    StoreUnavailable,
)
from sincpro_framework.sincpro_logger import logger

REVOKED = b"1"


class KeyValueRevocations(Revocations):
    """Revocations kept as keys with a time to live, under `prefix`."""

    def __init__(
        self,
        store: KeyValueStore | None = None,
        prefix: str = "sincpro:auth:revoked",
        on_unavailable: WhenUnavailable = WhenUnavailable.REFUSE,
        now: Callable[[], datetime] = partial(datetime.now, UTC),
        bypass_for: timedelta = timedelta(seconds=30),
    ) -> None:
        """`store` pins one instead of the running context's; `now` is the clock `until` is
        measured against."""
        self._store = store
        self.prefix = prefix
        self.on_unavailable = on_unavailable
        self.now = now
        self._bypass = StoreBypass(bypass_for, time.monotonic)

    def store(self) -> KeyValueStore:
        """The store given, else the running context's — read at every call."""
        return self._store if self._store is not None else key_value_store_of(current_store())

    def _key(self, kind: str, value: str) -> str:
        """`<prefix>:<kind>:<sha256 of value>`."""
        return f"{self.prefix}:{kind}:{digest(value)}"

    def _ttl(self, until: datetime) -> timedelta | None:
        """How long an entry lives — until `until`; `None` when that moment already passed."""
        left = until - self.now()
        return left if left > timedelta(0) else None

    def revoke_token(self, token_id: str, until: datetime) -> None:
        """`<prefix>:jti:<digest>`, living until `until` — nothing when it already passed."""
        if (ttl := self._ttl(until)) is not None:
            self.store().set(self._key("jti", token_id), REVOKED, ttl)

    def revoke_session(self, session: str, until: datetime) -> None:
        """`<prefix>:sid:<digest>`, living until `until` — nothing when it already passed."""
        if (ttl := self._ttl(until)) is not None:
            self.store().set(self._key("sid", session), REVOKED, ttl)

    def revoke_subject(self, subject: str, until: datetime) -> None:
        """`<prefix>:nbf:<digest>` holding this second: what was issued before it is refused —
        to the second, as a token's `iat` says it, so one issued later in this same second is
        never taken for an older one."""
        if (ttl := self._ttl(until)) is not None:
            moment = str(int(self.now().timestamp())).encode()
            self.store().set(self._key("nbf", subject), moment, ttl)

    def bump_permissions(self, subject: str) -> int:
        """`<prefix>:pv:<digest>` incremented by the store — atomic on every replica."""
        return self.store().increment(self._key("pv", subject))

    def _held(
        self,
        subject: str,
        token_id: str | None,
        session: str | None,
        related: tuple[str, ...],
    ) -> RevocationState:
        """The state as the store holds it — one `get_many`."""
        keys = [self._key("nbf", subject), self._key("pv", subject)]
        keys += [self._key("pv", one) for one in related]
        if token_id is not None:
            keys.append(self._key("jti", token_id))
        if session is not None:
            keys.append(self._key("sid", session))
        found = dict(zip(keys, self.store().get_many(keys), strict=True))
        not_before = found[keys[0]]
        version = found[keys[1]]
        versions = {one: found[self._key("pv", one)] for one in related}
        return RevocationState(
            token_revoked=token_id is not None
            and found[self._key("jti", token_id)] is not None,
            session_revoked=session is not None
            and found[self._key("sid", session)] is not None,
            not_before=(
                None if not_before is None else datetime.fromtimestamp(float(not_before), UTC)
            ),
            permission_version=0 if version is None else int(version),
            versions={
                one: 0 if held is None else int(held) for one, held in versions.items()
            },
        )

    def read(
        self,
        subject: str,
        token_id: str | None,
        session: str | None,
        related: tuple[str, ...],
    ) -> RevocationState:
        """Everything revoked about this credential — or, with `WhenUnavailable.ADMIT` and the
        store not answering, nothing: logged, and the store left alone for a while.

        1. Admitting and the store bypassed: nothing revoked.
        2. The store read in one `get_many`.
           2.1 It does not answer: refusing lets `StoreUnavailable` through (503); admitting
               logs a warning, trips the bypass and answers nothing revoked.
        Final: the state the store holds.
        """
        if self.on_unavailable == WhenUnavailable.ADMIT and self._bypass.is_open():
            return RevocationState()
        try:
            return self._held(subject, token_id, session, related)
        except StoreUnavailable:
            if self.on_unavailable == WhenUnavailable.REFUSE:
                raise
            logger.warning("revocations store unavailable: credentials admitted unchecked")
            self._bypass.trip()
            return RevocationState()


class InMemoryRevocations(KeyValueRevocations):
    """Revocations in this process — a test, a CLI, one replica; forgotten when it ends."""

    def __init__(self, now: Callable[[], datetime] = partial(datetime.now, UTC)) -> None:
        super().__init__(InMemoryKeyValue(now=now), now=now)
