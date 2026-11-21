"""Where the refresh tokens a `TokenIssuer` issued live — in this process, or on a `KeyValueStore`
every replica shares. A table of the project's is `DatabaseRefreshTokens`, in `data_layer.orm`.

Context: kept by digest, each record lives until its session ends; rotating is a
compare-and-set on the record, so of two refreshes racing with one token exactly one wins — the
other is told the token was already used. The live token of each session is indexed per subject
for a "signed in on" screen.
"""

import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import partial

from sincpro_framework.auth.accounts.domain.refresh_tokens import RefreshRecord, RefreshTokens
from sincpro_framework.auth.infrastructure.digests import digest
from sincpro_framework.context.entrypoint.resolution import current_store, key_value_store_of
from sincpro_framework.data_layer.store import KeyValueStore
from sincpro_framework.exceptions import ServiceUnavailableError


class InMemoryRefreshTokens(RefreshTokens):
    """Refresh tokens in this process — a test, a CLI, one replica."""

    def __init__(self, now: Callable[[], datetime] = partial(datetime.now, UTC)) -> None:
        self.now = now
        self._records: dict[str, RefreshRecord] = {}
        self._lock = threading.Lock()

    def save(self, record: RefreshRecord) -> None:
        """Kept by its digest, in this process."""
        with self._lock:
            self._records[record.digest] = record

    def find(self, digest: str) -> RefreshRecord | None:
        """The record of `digest` — `None` when unknown."""
        with self._lock:
            return self._records.get(digest)

    def rotate(self, digest: str, successor: RefreshRecord, at: datetime) -> bool:
        """Under one lock: the token marked rotated at `at` and its successor kept — `False`
        when it is unknown or was rotated already."""
        with self._lock:
            held = self._records.get(digest)
            if held is None or held.rotated_at is not None:
                return False
            self._records[digest] = held.model_copy(update={"rotated_at": at})
            self._records[successor.digest] = successor
            return True

    def sessions_of(self, subject: str) -> list[RefreshRecord]:
        """The subject's tokens not rotated, nor past their expiry or their session's end —
        oldest first."""
        now = self.now()
        with self._lock:
            return sorted(
                (
                    one
                    for one in self._records.values()
                    if one.subject == subject
                    and one.rotated_at is None
                    and now < min(one.expires_at, one.session_ends)
                ),
                key=lambda one: one.issued_at,
            )


class KeyValueRefreshTokens(RefreshTokens):
    """Refresh tokens on a `KeyValueStore`, each record living until its session ends."""

    def __init__(
        self,
        store: KeyValueStore | None = None,
        prefix: str = "sincpro:auth:refresh",
        now: Callable[[], datetime] = partial(datetime.now, UTC),
    ) -> None:
        self._store = store
        self.prefix = prefix
        self.now = now

    def store(self) -> KeyValueStore:
        """The store the tokens are kept in — the one given, else the running context's."""
        return self._store if self._store is not None else key_value_store_of(current_store())

    def _key(self, digest: str) -> str:
        return f"{self.prefix}:token:{digest}"

    def _index_key(self, subject: str) -> str:
        return f"{self.prefix}:sessions:{digest(subject)}"

    def _ttl(self, record: RefreshRecord) -> timedelta | None:
        left = record.session_ends - self.now()
        return left if left > timedelta(0) else None

    def _indexed(self, record: RefreshRecord) -> None:
        """The subject's index maps the record's session to its live token — compare-and-set."""
        store = self.store()
        key = self._index_key(record.subject)
        for _ in range(64):
            raw = store.get(key)
            sessions = {} if raw is None else json.loads(raw)
            sessions[record.family] = record.digest
            written = json.dumps(sessions, sort_keys=True).encode()
            if raw is None and store.add(key, written, self._ttl(record)):
                return
            if raw is not None and store.replace(key, raw, written):
                return
        raise ServiceUnavailableError("refresh token sessions are contended: try again")

    def save(self, record: RefreshRecord) -> None:
        """Under its digest, living until its session ends, and indexed under its subject by
        compare-and-set."""
        self.store().set(
            self._key(record.digest), record.model_dump_json().encode(), self._ttl(record)
        )
        self._indexed(record)

    def find(self, digest: str) -> RefreshRecord | None:
        """The record of `digest` — `None` when unknown or past its session."""
        raw = self.store().get(self._key(digest))
        return None if raw is None else RefreshRecord.model_validate_json(raw)

    def rotate(self, digest: str, successor: RefreshRecord, at: datetime) -> bool:
        """Compare-and-set: the record marked rotated only if it still holds what was read —
        of racing rotations one wins — and the successor saved after."""
        store = self.store()
        raw = store.get(self._key(digest))
        if raw is None:
            return False
        held = RefreshRecord.model_validate_json(raw)
        if held.rotated_at is not None:
            return False
        rotated = held.model_copy(update={"rotated_at": at}).model_dump_json().encode()
        if not store.replace(self._key(digest), raw, rotated, self._ttl(held)):
            return False
        self.save(successor)
        return True

    def sessions_of(self, subject: str) -> list[RefreshRecord]:
        """The subject's index read, and of its tokens the ones not rotated, nor past their
        expiry or their session's end — oldest first."""
        raw = self.store().get(self._index_key(subject))
        if raw is None:
            return []
        now = self.now()
        found = [self.find(one) for one in json.loads(raw).values()]
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
