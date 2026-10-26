"""What a context store saves beside its contexts with a version: a draft, a pipeline run — the
machinery each store implements once.

    InMemoryEntries(ttl)                       one process: tests, a single worker
    KeyValueEntries(RedisKeyValue(redis), ttl)  every replica sharing Redis, Valkey or Memcached

The same contract on both: a save wins only over the version it read, every entry lasts a TTL from
its last save, and a version is never handed out twice for a key — not after a discard, not after
an expiry — so a writer still holding an old one cannot win. An entry's key names its folder,
everything before its last `:`, and `listed(folder)` answers the live entries of one.

Context: what lives here is not a concept a project sees. `InMemoryContexts` and `KeyValueContexts`
are built on it; a project reaches it through the context store — `save_entry`, `get_entry`,
`delete_entry`, `list_entries` — and through `use_context().drafts()` and
`PipelineRuns.of()`.
"""

import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

from pydantic_core import to_jsonable_python

from sincpro_framework.context.domain import exceptions
from sincpro_framework.context.domain.store import Entry
from sincpro_framework.data_layer.store import KeyValueStore

LISTED = 1000
"""How many entries of one folder a key-value store can list, the most recently saved first."""


class Entries(ABC):
    """What every store of entries does; the version arithmetic is written once, here."""

    @abstractmethod
    def save(self, key: str, values: Mapping[str, Any], expected: int) -> Entry:
        """`values` under `key` with a new version, when `expected` is the live version — `0` for
        none; anything else raises `EntryConflict`. The TTL starts again."""

    @abstractmethod
    def get(self, key: str) -> Entry | None:
        """The live entry, or `None` when there is none, it was discarded or it expired."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Drop the entry, its version spent; one with none is not an error."""

    @abstractmethod
    def listed(self, folder: str) -> list[str]:
        """The keys of the live entries of `folder`, the most recently saved first."""

    def _milliseconds(self, moment: datetime) -> int:
        """Where a chain started from nothing begins: the clock, above any version a chain that
        ended a TTL ago could have reached."""
        return int(moment.timestamp() * 1000)

    def _check(self, key: str, expected: int, current: int, live: bool) -> None:
        """A save wins only over what it read: the live version, or `0` when none is live."""
        if expected != (current if live else 0):
            raise exceptions.EntryConflict(key, expected, current if live else 0)

    def _entry(
        self, key: str, values: Mapping[str, Any], version: int, now: datetime
    ) -> Entry:
        """The `Entry` saved for `values` — turned into what JSON carries (a decimal as text, a
        date as ISO text) now, so a reader in any store gets the same shape back."""
        return Entry(
            key=key, values=to_jsonable_python(dict(values)), version=version, saved_at=now
        )


class InMemoryEntries(Entries):
    """Entries in this process's memory, under one lock — versions won by the lock instead of
    an atomic store operation."""

    def __init__(
        self, ttl: timedelta, now: Callable[[], datetime] = partial(datetime.now, UTC)
    ) -> None:
        """`now` is the clock expiry is judged by — a `ManualClock.now` in tests.

        The last version of a key is remembered for a TTL after its entry was last saved or
        discarded, then forgotten, so a process holding entries for months does not hold every
        key it ever saw. A chain started after that begins at the clock's milliseconds, and
        always above any version this store handed out for any key, so a version never repeats
        — not under a clock that stands still, not with a TTL shorter than the saves it holds.

            InMemoryEntries(timedelta(hours=1))
                ->  no entry, no version remembered; an entry lives an hour from its last save
            InMemoryEntries(timedelta(hours=1), now=clock.now)   ->  judged by a test's clock

        The floor every new chain starts above begins at 0."""
        self.ttl = ttl
        self.now = now
        self._stored: dict[str, tuple[Entry, datetime]] = {}
        self._versions: dict[str, tuple[int, datetime]] = {}
        self._floor = 0
        self._lock = threading.Lock()

    def _live(self, key: str) -> Entry | None:
        """The entry under `key` while its TTL lasts — an expired one is dropped as it is
        read. Called under the lock."""
        found = self._stored.get(key)
        if found is None:
            return None
        entry, until = found
        if until <= self.now():
            del self._stored[key]
            return None
        return entry

    def _forget_expired(self, moment: datetime) -> None:
        """Drop the versions nobody can still present: remembered past their TTL, no entry."""
        for key in [key for key, (_, until) in self._versions.items() if until <= moment]:
            if self._live(key) is None:
                del self._versions[key]

    def save(self, key: str, values: Mapping[str, Any], expected: int) -> Entry:
        """Saved under the lock.

        1. Versions nobody can present any more are forgotten.
        2. `expected` checked against the remembered version — `0` when no entry is live.
        3. The next version: the remembered one plus one; a key with none remembered starts at
           the clock's milliseconds, above every version this store ever handed out.
        Final: the entry saved and its version remembered, both for a TTL — a copy answered.

            entries.save("drafts:acme:d1", {"total": Decimal("10.5")}, 0)
                ->  Entry(key="drafts:acme:d1", values={"total": "10.5"},
                          version=1729753200000, ...)      # the clock's milliseconds
            entries.save("drafts:acme:d1", {...}, 1729753200000)   ->  version 1729753200001
            entries.save("drafts:acme:d1", {...}, 0)               ->  EntryConflict

        The values are saved as JSON carries them, so a decimal comes back as its text.
        """
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
            entry = self._entry(key, values, version, moment)
            self._stored[key] = (entry, moment + self.ttl)
            self._versions[key] = (version, moment + self.ttl)
            return entry.model_copy(deep=True)

    def get(self, key: str) -> Entry | None:
        """A copy: changing what a caller holds never changes what is saved."""
        with self._lock:
            live = self._live(key)
            return None if live is None else live.model_copy(deep=True)

    def listed(self, folder: str) -> list[str]:
        """Every live key whose folder is `folder` — scanned in memory, no `LISTED` cap — the
        most recently saved first.

            listed("SINCPRO_ENTITY_DRAFT_CONTEXT:tenant_id=acme")
            ->  ["SINCPRO_ENTITY_DRAFT_CONTEXT:tenant_id=acme:invoice%3A7", …]
        """
        with self._lock:
            live = [
                entry
                for key in list(self._stored)
                if key.rsplit(":", 1)[0] == folder and (entry := self._live(key)) is not None
            ]
        live.sort(key=lambda entry: entry.saved_at, reverse=True)
        return [entry.key for entry in live]

    def delete(self, key: str) -> None:
        """The version is remembered a TTL more: a tab holding the entry still loses."""
        with self._lock:
            if self._stored.pop(key, None) is not None:
                version, _ = self._versions[key]
                self._versions[key] = (version, self.now() + self.ttl)


GONE = b"gone"
"""What a discard writes as the next version of a key: the slot is spent like any other, so the
number is never handed out again and a tab holding the entry before it loses."""

DEAD = b"dead"
"""What a save writes in the first slot of a start it judged stopped: the slot is spent, so the
save that took the start mark, if it was only slow, loses its `add` there instead of writing a
entry nobody can reach. Read as no entry, like `GONE`."""

SPENT = (GONE, DEAD)
"""What a slot holds when no entry is live there."""

RESTART_WINDOW = timedelta(seconds=10)
"""How long a chain being started from nothing may take to write its first slot before the
others judge it stopped. Liveness only: a start younger than this makes the others lose; an
older one has its first slot spent as `DEAD`, and a save that was merely slower than this — or
ran on a clock that far behind — loses with `DraftConflict`. No save is lost in silence,
whatever the clocks say."""


class KeyValueEntries(Entries):
    """Entries on a shared `KeyValueStore` — versions won with the store's atomic `add`, so
    saves on every replica race safely without a lock of their own."""

    def __init__(
        self,
        store: KeyValueStore,
        ttl: timedelta,
        prefix: str = "sincpro:entries",
        now: Callable[[], datetime] = partial(datetime.now, UTC),
    ) -> None:
        """A entry is a chain of slots, one key per version, each written once with the store's
        atomic `add`: of two saves naming the same version, exactly one writes the next slot,
        and a slot is never deleted — its TTL removes it — so its number is never handed out
        again. Two keys say where the chain was last seen, the head and the chain, so the store
        evicting one, or a process stopping between the slot and them, loses nothing; every
        key carries the TTL.

        A chain nothing is left of starts again at the clock's milliseconds, above anything a
        tab could still hold from before the expiry. Correctness — no save answered and then
        lost, no two entries on one version — never depends on the clocks: every slot is won
        with `add`, and a start judged stopped has its first slot spent as `DEAD`. The clocks
        decide liveness (`RESTART_WINDOW`: a start slower than that loses with
        `DraftConflict`) and that a chain started over a fully expired one does not reuse its
        numbers, which holds while the replicas' clocks agree to within the TTL."""
        self.store = store
        self.ttl = ttl
        self.prefix = prefix
        self.now = now

    def _listing(self, folder: str) -> str:
        """Where `folder`'s list lives: its last position here, each position at
        `…:listed:<n>`."""
        return f"{self.prefix}:{folder}:listed"

    def _last_listed(self, folder: str) -> int:
        """The last position taken in `folder`'s list: from where it was last seen, probed forward
        past the ones taken since — a hint that lags, or was evicted, hides nothing."""
        held = self.store.get(self._listing(folder))
        last = 0 if held is None else int(held)
        while self.store.get(f"{self._listing(folder)}:{last + 1}") is not None:
            last += 1
        return last

    def _list(self, key: str) -> None:
        """`key` named in its folder's list, in a position won with the store's atomic `add` —
        as a version is — so saves that list at the same instant, on any replica, never lose one
        another, and every key of the list carries the TTL."""
        folder = key.rsplit(":", 1)[0]
        position = self._last_listed(folder) + 1
        while not self.store.add(
            f"{self._listing(folder)}:{position}", key.encode(), self.ttl
        ):
            position += 1
        self.store.set(self._listing(folder), str(position).encode(), self.ttl)

    def _head(self, key: str) -> str:
        """The first pointer to where `key`'s chain was last seen."""
        return f"{self.prefix}:{key}:head"

    def _chain(self, key: str) -> str:
        """The second pointer — saved apart from the head so evicting one loses nothing."""
        return f"{self.prefix}:{key}:chain"

    def _slot(self, key: str, version: int) -> str:
        """The slot of one version: written once with `add`, never deleted."""
        return f"{self.prefix}:{key}:{version}"

    def _start(self, key: str, seen: int) -> str:
        """The start mark of a chain begun again after version `seen` — the meeting point of
        the saves that all saw nothing live there."""
        return f"{self.prefix}:{key}:start:{seen}"

    def _point(self, key: str, version: int) -> None:
        """Both pointers raised to `version`, the TTL started again. A pointer already past it —
        a later save moved it first — stays; readers probe forward from the larger, so one that
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
        3. The slots after it are probed until one is missing: a save that won its `add` and
           has not moved the pointers yet is the latest.
        Final: the version, its slot (`GONE` or `DEAD` when no entry is live there) and
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
                        # built on it, so its save, if only slow, loses its `add` there.
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

    def get(self, key: str) -> Entry | None:
        """The latest slot read back as an `Entry` — `None` when it is missing, `GONE` or
        `DEAD`."""
        _, found, _ = self._latest(key)
        if found is None or found in SPENT:
            return None
        return Entry.model_validate_json(found)

    def listed(self, folder: str) -> list[str]:
        """The live entries of `folder`, the most recently saved first — of the last `LISTED`
        named in it.

            entries.listed("drafts:acme")   ->  ["drafts:acme:d2", "drafts:acme:d1"]
            entries.listed("drafts:beta")   ->  []          # nothing saved, or all expired

        A key named twice in the list is answered once; one discarded or expired, not at all.
        """
        last = self._last_listed(folder)
        names = self.store.get_many(
            [
                f"{self._listing(folder)}:{one}"
                for one in range(last, max(0, last - LISTED), -1)
            ]
        )
        keys = list(dict.fromkeys(one.decode() for one in names if one is not None))
        live = [(key, entry) for key in keys if (entry := self.get(key)) is not None]
        live.sort(key=lambda pair: pair[1].saved_at, reverse=True)
        return [key for key, _ in live]

    def _restart(self, key: str, latest: int, expected: int) -> int:
        """Where a chain nothing is left of begins again. Only one of the saves that saw the
        same nothing may start it: they meet on the start mark, which says where the chain
        begins and when it was started."""
        moment = self._milliseconds(self.now())
        version = max(latest + 1, moment)
        if not self.store.add(
            self._start(key, latest), f"{version}:{moment}".encode(), self.ttl
        ):
            raise exceptions.EntryConflict(key, expected, 0)
        return version

    def save(self, key: str, values: Mapping[str, Any], expected: int) -> Entry:
        """Written to the next slot of `key`'s chain, won with `add`.

        1. The latest version and whether it is live; a chain another save is starting
           right now makes this one lose.
        2. `expected` has to be that version, or `0` when none is live.
        3. The next slot is written with `add`: losing it means another save came first. A
           chain started again takes the first free slot from its start, past the slots of a
           chain whose pointers and marks were lost; its first slot spent as `DEAD` means the
           start was judged stopped, and this save lost.
        Final: the pointers move to it, and an entry that was not live is named in its
        folder's list.

            entries.save("drafts:acme:d1", {"total": 10}, 0)
                ->  Entry(version=1729753200000), in slot "…:drafts:acme:d1:1729753200000"
            entries.save("drafts:acme:d1", {"total": 12}, 1729753200000)
                ->  Entry(version=1729753200001)
            two replicas saving with the same `expected`
                ->  one wins, the other `EntryConflict`

        Nothing is overwritten: every version is a slot of its own, written once."""
        latest, found, starting = self._latest(key)
        live = found is not None and found not in SPENT
        self._check(key, expected, latest, live)
        if starting:
            raise exceptions.EntryConflict(key, expected, 0)
        moment = self.now()
        if found is not None:
            version = latest + 1
            entry = self._entry(key, values, version, moment)
            if not self.store.add(
                self._slot(key, version), entry.model_dump_json().encode(), self.ttl
            ):
                raise exceptions.EntryConflict(key, expected, version)
        else:
            version = self._restart(key, latest, expected)
            entry = self._entry(key, values, version, moment)
            while not self.store.add(
                self._slot(key, version), entry.model_dump_json().encode(), self.ttl
            ):
                if self.store.get(self._slot(key, version)) == DEAD:
                    raise exceptions.EntryConflict(key, expected, 0)
                version += 1
                entry = self._entry(key, values, version, moment)
        self._point(key, version)
        if not live:
            self._list(key)
        return entry

    def delete(self, key: str) -> None:
        """The next slot is written as `GONE`, so the number is spent and a tab holding the
        entry before it loses. A save that wins that slot first is discarded in turn.

            entries.delete("drafts:acme:d1")   # live at version 7
                ->  slot 8 written GONE; get("drafts:acme:d1")  ->  None
            entries.delete("drafts:acme:d9")   ->  nothing: no live entry

        Tried until the `GONE` slot is won, or nothing live is left to discard."""
        while True:
            latest, found, _ = self._latest(key)
            if found is None or found in SPENT:
                return
            if self.store.add(self._slot(key, latest + 1), GONE, self.ttl):
                self._point(key, latest + 1)
                return
