"""Where the refresh tokens a `TokenIssuer` issued live — on a `KeyValueStore` every replica
shares (`KeyValueRefreshTokens`), or in this process (`InMemoryRefreshTokens`, the same over an
`InMemoryKeyValue`). A table of the project's is `DatabaseRefreshTokens`, in `data_layer.orm`.

    KeyValueRefreshTokens()                         the running context's store
    KeyValueRefreshTokens(RedisKeyValue(...))       one given
    InMemoryRefreshTokens()                         a test, a CLI, one replica

Context: stored by digest, each record lives until its session ends — at least a second, never
without an expiry — so the store sweeps what ended. Rotating is a compare-and-set on the record,
so of two refreshes racing with one token exactly one wins — the other is told the token was
already used. The live token of each session is indexed per subject for a "signed in on" screen:
the index drops the sessions that ended and lives as long as its last one, so it never grows with
every session a person ever opened.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import partial

from sincpro_framework.auth.accounts.domain.refresh_tokens import RefreshRecord, RefreshTokens
from sincpro_framework.auth.common.adapters.key_value import KeyValueStoreMixin
from sincpro_framework.auth.common.infrastructure.digests import digest
from sincpro_framework.data_layer.store import InMemoryKeyValue, KeyValueStore
from sincpro_framework.exceptions import ServiceUnavailableError
from sincpro_framework.sincpro_logger import logger

MIN_TTL = timedelta(seconds=1)
"""The shortest a record is stored — one whose session already ended still expires."""


class KeyValueRefreshTokens(KeyValueStoreMixin, RefreshTokens):
    """Refresh tokens on a `KeyValueStore`, each record living until its session ends."""

    warn_when_unshared = True

    def __init__(
        self,
        store: KeyValueStore | None = None,
        prefix: str = "sincpro:auth:refresh",
        now: Callable[[], datetime] = partial(datetime.now, UTC),
    ) -> None:
        """`store` pins one instead of the running context's."""
        self._init_key_value_store(store, prefix)
        self.now = now

    def _key(self, digest: str) -> str:
        return f"{self.prefix}:token:{digest}"

    def _index_key(self, subject: str) -> str:
        return f"{self.prefix}:sessions:{digest(subject)}"

    def _ttl(self, record: RefreshRecord) -> timedelta:
        """How long `record` is stored: until its session ends — `MIN_TTL` at least."""
        return max(record.session_ends - self.now(), MIN_TTL)

    def _index_session(self, record: RefreshRecord) -> None:
        """The subject's index maps each live session to its token and its end — the record's
        session written in, the ended ones dropped, the index living until its last session
        ends — by compare-and-set."""

        def indexed(raw: bytes | None) -> tuple[bytes, timedelta]:
            now = self.now().timestamp()
            sessions = {
                session: entry
                for session, entry in ({} if raw is None else json.loads(raw)).items()
                if entry["ends"] > now
            }
            sessions[record.session] = {
                "token": record.digest,
                "ends": record.session_ends.timestamp(),
            }
            lives = timedelta(seconds=max(one["ends"] for one in sessions.values()) - now)
            return json.dumps(sessions, sort_keys=True).encode(), max(lives, MIN_TTL)

        self.update(self._index_key(record.subject), indexed)

    def save(self, record: RefreshRecord) -> None:
        """Under its digest, living until its session ends, and indexed under its subject by
        compare-and-set."""
        self.store().set(
            self._key(record.digest), record.model_dump_json().encode(), self._ttl(record)
        )
        self._index_session(record)

    def find(self, digest: str) -> RefreshRecord | None:
        """The record of `digest` — `None` when unknown or past its session."""
        raw = self.store().get(self._key(digest))
        return None if raw is None else RefreshRecord.model_validate_json(raw)

    def rotate(self, digest: str, successor: RefreshRecord, at: datetime) -> bool:
        """The record marked rotated only if it still holds what was read — of racing rotations
        one wins — its successor stored before it.

        1. Unknown, or rotated already: `False`.
        2. The successor stored first: a store failing before the mark leaves the token unspent
           — the client retries with it — never a session whose live token was never stored.
        3. The mark, by compare-and-set; lost, the successor is deleted: `False`.
        Final: the subject's index pointed at the successor — a store failing there leaves the
        session working, only missing from "signed in on" until its next refresh.
        """
        store = self.store()
        raw = store.get(self._key(digest))
        if raw is None:
            return False
        current = RefreshRecord.model_validate_json(raw)
        if current.rotated_at is not None:
            return False
        store.set(
            self._key(successor.digest),
            successor.model_dump_json().encode(),
            self._ttl(successor),
        )
        rotated = current.model_copy(update={"rotated_at": at}).model_dump_json().encode()
        if not store.replace(self._key(digest), raw, rotated, self._ttl(current)):
            store.delete(self._key(successor.digest))
            return False
        try:
            self._index_session(successor)
        except ServiceUnavailableError:
            logger.warning(
                "a refreshed session was not indexed: it works, missing from its subject's list",
                session=successor.session[:12],
            )
        return True

    def sessions_of(self, subject: str) -> list[RefreshRecord]:
        """The subject's index read, and of its tokens the ones not rotated, nor past their
        expiry or their session's end — oldest first."""
        raw = self.store().get(self._index_key(subject))
        if raw is None:
            return []
        now = self.now()
        found = [self.find(one["token"]) for one in json.loads(raw).values()]
        return sorted(
            (
                one
                for one in found
                if one is not None
                and one.rotated_at is None
                and now < min(one.expires_at, one.session_ends)
            ),
            key=lambda one: one.issued_at,
        )


class InMemoryRefreshTokens(KeyValueRefreshTokens):
    """Refresh tokens in this process — a test, a CLI, one replica; what ended is swept."""

    def __init__(self, now: Callable[[], datetime] = partial(datetime.now, UTC)) -> None:
        super().__init__(InMemoryKeyValue(now=now), now=now)
