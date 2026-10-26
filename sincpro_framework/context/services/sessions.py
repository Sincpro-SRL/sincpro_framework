"""`Sessions`: a login that lasts while it is used — one sign-in, and the client never asked again.

    sessions = use_context().sessions()                      30 days unused, no limit while used
    sessions = use_context().sessions(idle_for=None)         never lapses: until closed
    sessions = use_context().sessions(timedelta(hours=8), at_most=timedelta(days=7))

    opened = sessions.open({"user_id": "ana", "token": token})    ->  Session(id="9fK…", …)
    sessions.get(opened.id)          ->  the session, its expiry moved: one more `idle_for`
    sessions.save(opened.id, {...})  ->  its values replaced, the same id and opening
    sessions.take(code)              ->  read once and gone: an OAuth code, a magic link
    sessions.close(opened.id)        ->  signed out, on every replica

**Sliding, not fixed.** Every `get` gives the session `idle_for` more (`KeyValueStore.touch` —
the value is not written again), so a client that keeps using it is never asked to sign in
again, and one abandoned lapses on its own. `at_most` caps a session however much it is used;
none by default. `idle_for=None` never lapses: it lasts until `close`.

A session lives on the `KeyValueStore` the context is stored on — Redis or Valkey shared by
every replica, this process's memory with nothing set — under `session:<namespace>:<id>`. Its
values are JSON: text, numbers, booleans, lists and mappings of them. While the store does not
answer, a session cannot be read or opened (`StoreUnavailable`): a login cannot pretend to be
known.
"""

import json
import secrets
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

from sincpro_framework.context.domain.sessions import Session
from sincpro_framework.data_layer.store import KeyValueStore

DEFAULT_IDLE_FOR = timedelta(days=30)
"""How long a session lasts unused, by default — a month, renewed at every use."""


class Sessions:
    """The sessions of one namespace on a `KeyValueStore` — how long each lasts unused, and at
    most."""

    def __init__(
        self,
        store: KeyValueStore,
        idle_for: timedelta | None = DEFAULT_IDLE_FOR,
        at_most: timedelta | None = None,
        namespace: str = "sessions",
        now: Callable[[], datetime] = partial(datetime.now, UTC),
    ) -> None:
        """`idle_for` is how long a session lasts unused — `None`, until closed; `at_most` how
        long it lasts however much it is used — `None`, no limit; `namespace` keeps these apart
        from other sessions on the store; `now` is the clock — a `ManualClock.now` in tests,
        beside the same clock on the store."""
        self.store = store
        self.idle_for = idle_for
        self.at_most = at_most
        self.namespace = namespace
        self.now = now

    def _key(self, session_id: str) -> str:
        """Where a session is stored: `session:<namespace>:<id>`."""
        return f"session:{self.namespace}:{session_id}"

    def _lasts(self, opened_at: datetime, now: datetime) -> timedelta | None:
        """How long a session opened at `opened_at` lasts from `now` if unused: `idle_for`, never
        past `at_most` from its opening; `None` when neither bounds it."""
        bounds = [] if self.idle_for is None else [self.idle_for]
        if self.at_most is not None:
            bounds.append(opened_at + self.at_most - now)
        return min(bounds) if bounds else None

    def _session(
        self,
        session_id: str,
        values: dict[str, Any],
        opened_at: datetime,
        lasts: timedelta | None,
    ) -> Session:
        """The session as a caller reads it — when it lapses counted from now."""
        expires_at = None if lasts is None else self.now() + lasts
        return Session(
            id=session_id, values=values, opened_at=opened_at, expires_at=expires_at
        )

    def _read(self, held: bytes) -> tuple[dict[str, Any], datetime]:
        """The values and the opening a stored session says."""
        stored = json.loads(held)
        return stored["values"], datetime.fromisoformat(stored["opened_at"])

    def _written(self, values: Mapping[str, Any], opened_at: datetime) -> bytes:
        """A session as the store holds it: its values and its opening, as JSON."""
        return json.dumps(
            {"values": dict(values), "opened_at": opened_at.isoformat()},
            separators=(",", ":"),
        ).encode()

    def open(self, values: Mapping[str, Any]) -> Session:
        """A new session holding `values`, under an id nobody can guess — what the client is
        handed and sends back.

            sessions.open({"user_id": "ana"})   ->  Session(id="9fK…", values={"user_id": "ana"})
        """
        session_id = secrets.token_urlsafe(32)
        opened_at = self.now()
        lasts = self._lasts(opened_at, opened_at)
        self.store.set(self._key(session_id), self._written(values, opened_at), lasts)
        return self._session(session_id, dict(values), opened_at, lasts)

    def get(self, session_id: str) -> Session | None:
        """The session under `session_id`, given `idle_for` more — `None` when there is none,
        or it lapsed.

        1. Nothing stored under it: `None`.
        2. Past `at_most` since it opened: closed, `None`.
        3. Final: its expiry moved (`touch`, its value not written again) and the session
           answered; gone in between, `None`.
        """
        held = self.store.get(self._key(session_id))
        if held is None:
            return None
        values, opened_at = self._read(held)
        now = self.now()
        if self.at_most is not None and now >= opened_at + self.at_most:
            self.store.delete(self._key(session_id))
            return None
        lasts = self._lasts(opened_at, now)
        if not self.store.touch(self._key(session_id), lasts):
            return None
        return self._session(session_id, values, opened_at, lasts)

    def save(self, session_id: str, values: Mapping[str, Any]) -> Session | None:
        """The session under `session_id` holding `values` from now on — the same id and
        opening, given `idle_for` more; `None` when it is gone. Written over exactly what was
        read, so a session closed meanwhile is never brought back."""
        key = self._key(session_id)
        held = self.store.get(key)
        if held is None:
            return None
        _, opened_at = self._read(held)
        lasts = self._lasts(opened_at, self.now())
        if not self.store.replace(key, held, self._written(values, opened_at), lasts):
            return None
        return self._session(session_id, dict(values), opened_at, lasts)

    def take(self, session_id: str) -> Session | None:
        """The session under `session_id`, read and gone in one step — of many callers racing
        for it, one gets it: an OAuth code, a magic link, anything used once."""
        held = self.store.take(self._key(session_id))
        if held is None:
            return None
        values, opened_at = self._read(held)
        return self._session(session_id, values, opened_at, None)

    def close(self, session_id: str) -> None:
        """The session under `session_id` gone — signed out, on every replica."""
        self.store.delete(self._key(session_id))
