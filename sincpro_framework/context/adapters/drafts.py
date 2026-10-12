"""The draft stores the framework ships — the ones each context store gives (`ContextStore.drafts()`).

    InMemoryDrafts(ttl)                    one process: tests, a single worker
    KeyValueDrafts(RedisKeyValue(redis), ttl)   every replica sharing Redis, Valkey or Memcached

The same contract on both: a keep wins only over the version it read, every draft lasts a TTL from
its last keep, and a version is never handed out twice for a key.
"""

import json
import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

from sincpro_framework.common.store import KeyValueStore
from sincpro_framework.context.domain.drafts import Draft, DraftConflict, DraftStore


class InMemoryDrafts(DraftStore):
    def __init__(
        self, ttl: timedelta, now: Callable[[], datetime] = partial(datetime.now, UTC)
    ) -> None:
        """`now` is the clock expiry is judged by — a `ManualClock.now` in tests.

        The last version of a key is remembered for a TTL after its draft was last kept or
        discarded, then forgotten, so a process holding drafts for months does not hold every
        key it ever saw. A chain started after that begins at the clock's milliseconds, and
        always above any version this store handed out for any key, so a version never repeats
        — not under a clock that stands still, not with a TTL shorter than the keeps it holds.
        """
        self.ttl = ttl
        self.now = now
        self._kept: dict[str, tuple[Draft, datetime]] = {}
        self._versions: dict[str, tuple[int, datetime]] = {}
        self._floor = 0
        self._lock = threading.Lock()

    def _live(self, key: str) -> Draft | None:
        found = self._kept.get(key)
        if found is None:
            return None
        draft, until = found
        if until <= self.now():
            del self._kept[key]
            return None
        return draft

    def _forget_expired(self, moment: datetime) -> None:
        """Drop the versions nobody can still present: remembered past their TTL, no draft."""
        for key in [key for key, (_, until) in self._versions.items() if until <= moment]:
            if self._live(key) is None:
                del self._versions[key]

    def _keep(
        self, key: str, values: Mapping[str, Any], origin_version: int, expected: int
    ) -> Draft:
        with self._lock:
            moment = self.now()
            self._forget_expired(moment)
            remembered = self._versions.get(key)
            current = 0 if remembered is None else remembered[0]
            self._check(key, expected, current, self._live(key) is not None)
            version = (
                max(self._milliseconds(moment), self._floor + 1)
                if remembered is None
                else remembered[0] + 1
            )
            self._floor = max(self._floor, version)
            draft = self._drafted(key, values, version, origin_version, moment)
            self._kept[key] = (draft, moment + self.ttl)
            self._versions[key] = (version, moment + self.ttl)
            return draft.model_copy(deep=True)

    def _read(self, key: str) -> Draft | None:
        """A copy: changing what a caller holds never changes what is kept."""
        with self._lock:
            live = self._live(key)
            return None if live is None else live.model_copy(deep=True)

    def subjects(self, owner: str) -> list[str]:
        with self._lock:
            mine = f"{owner}/"
            live = [
                draft
                for key in list(self._kept)
                if key.startswith(mine) and (draft := self._live(key)) is not None
            ]
        live.sort(key=lambda draft: draft.kept_at, reverse=True)
        return [draft.key.removeprefix(mine) for draft in live]

    def _discard(self, key: str) -> None:
        """The version is remembered a TTL more: a tab holding the draft still loses."""
        with self._lock:
            if self._kept.pop(key, None) is not None:
                version, _ = self._versions[key]
                self._versions[key] = (version, self.now() + self.ttl)


GONE = b"gone"
"""What a discard writes as the next version of a key: the slot is spent like any other, so the
number is never handed out again and a tab holding the draft before it loses."""

DEAD = b"dead"
"""What a keep writes in the first slot of a start it judged stopped: the slot is spent, so the
keep that took the start mark, if it was only slow, loses its `add` there instead of writing a
draft nobody can reach. Read as no draft, like `GONE`."""

SPENT = (GONE, DEAD)
"""What a slot holds when no draft is live there."""

RESTART_WINDOW = timedelta(seconds=10)
"""How long a chain being started from nothing may take to write its first slot before the
others judge it stopped. Liveness only: a start younger than this makes the others lose; an
older one has its first slot spent as `DEAD`, and a keep that was merely slower than this — or
ran on a clock that far behind — loses with `DraftConflict`. No keep is lost in silence,
whatever the clocks say."""


class KeyValueDrafts(DraftStore):
    def __init__(
        self,
        store: KeyValueStore,
        ttl: timedelta,
        prefix: str = "sincpro:draft",
        now: Callable[[], datetime] = partial(datetime.now, UTC),
    ) -> None:
        """A draft is a chain of slots, one key per version, each written once with the store's
        atomic `add`: of two keeps naming the same version, exactly one writes the next slot,
        and a slot is never deleted — its TTL removes it — so its number is never handed out
        again. Two keys say where the chain was last seen, the head and the chain, so the store
        evicting one, or a process stopping between the slot and them, loses nothing; every
        key carries the TTL.

        A chain nothing is left of starts again at the clock's milliseconds, above anything a
        tab could still hold from before the expiry. Correctness — no keep answered and then
        lost, no two drafts on one version — never depends on the clocks: every slot is won
        with `add`, and a start judged stopped has its first slot spent as `DEAD`. The clocks
        decide liveness (`RESTART_WINDOW`: a start slower than that loses with
        `DraftConflict`) and that a chain started over a fully expired one does not reuse its
        numbers, which holds while the replicas' clocks agree to within the TTL."""
        self.store = store
        self.ttl = ttl
        self.prefix = prefix
        self.now = now

    def _index(self, owner: str) -> str:
        return f"{self.prefix}:{owner}:subjects"

    def _listed(self, owner: str) -> list[str]:
        held = self.store.get(self._index(owner))
        return [] if held is None else list(json.loads(held))

    def keep(
        self,
        owner: str,
        subject: str,
        values: Mapping[str, Any],
        origin_version: int,
        expected: int,
    ) -> Draft:
        """The draft kept, and `subject` listed for `owner` — the list lasting the TTL from its
        last keep, like the drafts it names."""
        draft = super().keep(owner, subject, values, origin_version, expected)
        listed = self._listed(owner)
        self.store.set(
            self._index(owner),
            json.dumps([subject, *(one for one in listed if one != subject)]).encode(),
            self.ttl,
        )
        return draft

    def discard(self, owner: str, subject: str) -> None:
        super().discard(owner, subject)
        listed = self._listed(owner)
        if subject in listed:
            remaining = [one for one in listed if one != subject]
            self.store.set(self._index(owner), json.dumps(remaining).encode(), self.ttl)

    def subjects(self, owner: str) -> list[str]:
        """Context: the list is rewritten on every keep and discard, so two keeps of one owner
        at the same instant may each list only its own subject until the next keep; a draft is
        never lost by it — it is read by its subject all the same."""
        return [one for one in self._listed(owner) if self.read(owner, one) is not None]

    def _head(self, key: str) -> str:
        return f"{self.prefix}:{key}:head"

    def _chain(self, key: str) -> str:
        return f"{self.prefix}:{key}:chain"

    def _slot(self, key: str, version: int) -> str:
        return f"{self.prefix}:{key}:{version}"

    def _start(self, key: str, seen: int) -> str:
        return f"{self.prefix}:{key}:start:{seen}"

    def _point(self, key: str, version: int) -> None:
        """Both pointers raised to `version`, the TTL started again. A pointer already past it —
        a later keep moved it first — stays; readers probe forward from the larger, so one that
        lags for a moment hides nothing."""
        mark = str(version).encode()
        names = [self._head(key), self._chain(key)]
        for name, seen in zip(names, self.store.get_many(names), strict=True):
            if seen is None or int(seen) <= version:
                self.store.set(name, mark, self.ttl)

    def _latest(self, key: str) -> tuple[int, bytes | None, bool]:
        """The latest version of `key`, what its slot holds — `None` when nothing is live — and
        whether a chain is being started there right now.

        1. The head and the chain say where the chain was last seen; the larger wins, `0` when
           neither is there.
        2. A dead slot there, with nothing after it, may be a chain started from that point
           whose pointers are not written yet: its start mark says where it begins. A mark
           younger than `RESTART_WINDOW` whose first slot is missing is a start in progress;
           an older one is a start that stopped, and its first slot is spent as `DEAD`.
        3. The slots after it are probed until one is missing: a keep that won its `add` and
           has not moved the pointers yet is the latest.
        Final: the version, its slot (`GONE` or `DEAD` when no draft is live there) and
        whether a start is in progress; the pointers raised when they lagged or one was lost.
        """
        head, chain = self.store.get_many([self._head(key), self._chain(key)])
        pointers = [int(one) for one in (head, chain) if one is not None]
        version = max(pointers, default=0)
        found, after = self.store.get_many(
            [self._slot(key, version), self._slot(key, version + 1)]
        )
        starting = False
        if found is None and after is None:
            mark = self.store.get(self._start(key, version))
            if mark is not None:
                first, written = (int(one) for one in mark.split(b":"))
                found, after = self.store.get_many(
                    [self._slot(key, first), self._slot(key, first + 1)]
                )
                if found is None and after is None:
                    young = self._milliseconds(self.now()) - written
                    starting = young < RESTART_WINDOW.total_seconds() * 1000
                if not starting:
                    version = first
                    if found is None and after is None:
                        # A start judged stopped: its first slot is spent before anything is
                        # built on it, so its keep, if only slow, loses its `add` there.
                        self.store.add(self._slot(key, first), DEAD, self.ttl)
                        found, after = self.store.get_many(
                            [self._slot(key, first), self._slot(key, first + 1)]
                        )
        while after is not None:
            version, found = version + 1, after
            after = self.store.get(self._slot(key, version + 1))
        if found is not None and pointers != [version, version]:
            self._point(key, version)
        return version, found, starting

    def _restart(self, key: str, latest: int, expected: int) -> int:
        """Where a chain nothing is left of begins again. Only one of the keeps that saw the
        same nothing may start it: they meet on the start mark, which says where the chain
        begins and when it was started."""
        moment = self._milliseconds(self.now())
        version = max(latest + 1, moment)
        if not self.store.add(
            self._start(key, latest), f"{version}:{moment}".encode(), self.ttl
        ):
            raise DraftConflict(key, expected, 0)
        return version

    def _keep(
        self, key: str, values: Mapping[str, Any], origin_version: int, expected: int
    ) -> Draft:
        """1. The latest version and whether it is live; a chain another keep is starting
           right now makes this one lose.
        2. `expected` has to be that version, or `0` when none is live.
        3. The next slot is written with `add`: losing it means another keep came first. A
           chain started again takes the first free slot from its start, past the slots of a
           chain whose pointers and marks were lost; its first slot spent as `DEAD` means the
           start was judged stopped, and this keep lost.
        Final: the pointers move to it.
        """
        latest, found, starting = self._latest(key)
        live = found is not None and found not in SPENT
        self._check(key, expected, latest, live)
        if starting:
            raise DraftConflict(key, expected, 0)
        moment = self.now()
        if found is not None:
            version = latest + 1
            draft = self._drafted(key, values, version, origin_version, moment)
            if not self.store.add(
                self._slot(key, version), draft.model_dump_json().encode(), self.ttl
            ):
                raise DraftConflict(key, expected, version)
        else:
            version = self._restart(key, latest, expected)
            draft = self._drafted(key, values, version, origin_version, moment)
            while not self.store.add(
                self._slot(key, version), draft.model_dump_json().encode(), self.ttl
            ):
                if self.store.get(self._slot(key, version)) == DEAD:
                    raise DraftConflict(key, expected, 0)
                version += 1
                draft = self._drafted(key, values, version, origin_version, moment)
        self._point(key, version)
        return draft

    def _read(self, key: str) -> Draft | None:
        _, found, _ = self._latest(key)
        if found is None or found in SPENT:
            return None
        return Draft.model_validate_json(found)

    def _discard(self, key: str) -> None:
        """The next slot is written as `GONE`, so the number is spent and a tab holding the
        draft before it loses. A keep that wins that slot first is discarded in turn."""
        while True:
            latest, found, _ = self._latest(key)
            if found is None or found in SPENT:
                return
            if self.store.add(self._slot(key, latest + 1), GONE, self.ttl):
                self._point(key, latest + 1)
                return
