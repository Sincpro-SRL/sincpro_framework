"""The process level shared by every replica, through a store.

    use_context().root.share(KeyValueContexts(RedisKeyValue(redis)), "process:billing")
    use_context().root.set("maintenance", True)       every replica reads it within `every`

**Read locally, refreshed by version.** A read is the local mapping; at most once per `every` the
store's version is asked, and the values are read again only when it moved — never a network call per
read. **A write lands on the latest values** (`ContextStore.change`): a replica writing `plan` never
erases the `maintenance` another wrote, however stale its own copy was.

**Defaults from the code are added when the store lacks them**: a default a later release brings
reaches every replica; a value a replica saved wins over any default.

A value that does not travel — under a key that is not text, or an object, a store, a settings
object under one that is — stays in this process, and a refresh never drops it.
"""

import time
from collections.abc import Callable, Mapping
from datetime import timedelta
from types import MappingProxyType
from typing import Any

from sincpro_framework.context.domain.node import Values
from sincpro_framework.context.domain.store import ContextStore
from sincpro_framework.context.domain.travel import Travel


class SharedValues(Values):
    """`Values` a store shares across processes — `store` is the one they are shared through."""

    __slots__ = ("store", "_key", "_every", "_checked", "_version")

    def __init__(
        self,
        store: ContextStore,
        key: str,
        every: timedelta,
        seed: Mapping[Any, Any] | None = None,
    ) -> None:
        """Values under `key` in `store`, re-read at most once per `every`.

        1. Start from `seed` — what the process held before it joined the store.
        2. Each value of the seed under a text key that the store lacks is added: the first
           replica brings its defaults, a later release brings the ones it added.
        Final: read what the store holds now — a value another replica saved wins over the
        seed.

            SharedValues(store, "process:billing", timedelta(seconds=5), seed={"lang": "es"})
                ->  first replica: {"lang": "es"}, saved as "process:billing"
                ->  a later one, the store holding {"lang": "en"}: {"lang": "en"}
                ->  a release seeding {"lang": "es", "beta": True}: {"lang": "en", "beta": True}

        Nothing is refreshed after this until `every` has passed since the last check.
        """
        super().__init__()
        self.store = store
        self._key = key
        self._every = every.total_seconds()
        self._checked = 0.0
        self._version = -1
        self._current = MappingProxyType(dict(seed or {}))
        defaults = self.named()
        if defaults:
            store.change(key, lambda held: {**defaults, **held})
        self._refreshed()

    def _held_here(self) -> dict[Any, Any]:
        """What only this process holds: a value under a key that is not text, or one that
        does not travel (`Travel.stays`). A refresh keeps it; a store whose codec saves it
        (`PickleCodec`) answers its own, which wins."""
        stays = Travel.of(self._current).stays
        return {
            key: value
            for key, value in self._current.items()
            if not isinstance(key, str) or key in stays
        }

    def _refreshed(self) -> None:
        """Ask the store's version and read the values again only when it moved.

        1. Note the time of this check.
        2. The same version as last read: nothing to read.
        3. Otherwise the store's values, with what only this process holds left in place.
        Final: the version read remembered.
        """
        self._checked = time.monotonic()
        version = self.store.version(self._key)
        if version == self._version:
            return
        stored = self.store.load(self._key) or {}
        self._current = MappingProxyType({**self._held_here(), **stored})
        self._version = version

    @property
    def current(self) -> Mapping[Any, Any]:
        """The local values — refreshed first when `every` passed since the last check, so a
        read costs a store round trip at most once per `every`."""
        if time.monotonic() - self._checked >= self._every:
            self._refreshed()
        return self._current

    def _saved(self, changing: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        """Save `changing` on the latest values in the store, and hold what it answered beside
        what only this process holds — another replica's write since the last refresh is read
        in, never overwritten.

        Context: the version stays as last read — this write moved it, so the next check reads
        the values once more, with whatever another replica wrote right after."""
        with self._writing:
            changed = self.store.change(self._key, changing)
            self._current = MappingProxyType({**self._held_here(), **changed})

    def set(self, key: Any, value: Any) -> None:
        """Written locally at once, then saved for the other replicas — only that key."""
        super().set(key, value)
        if isinstance(key, str):
            self._saved(lambda held: {**held, key: value})

    def update(self, values: Mapping[Any, Any]) -> None:
        """Written locally at once, then saved for the other replicas — only those keys."""
        super().update(values)
        named = {key: value for key, value in values.items() if isinstance(key, str)}
        if named:
            self._saved(lambda held: {**held, **named})

    def unset(self, key: Any) -> None:
        """Taken away locally at once, then for the other replicas — only that key."""
        super().unset(key)
        if isinstance(key, str):
            self._saved(lambda held: {name: one for name, one in held.items() if name != key})

    def replace(self, values: Mapping[Any, Any]) -> None:
        """Replaced locally at once, then for the other replicas: the level says exactly
        `values`, whatever another replica saved before."""
        super().replace(values)
        named = {key: value for key, value in values.items() if isinstance(key, str)}
        self._saved(lambda held: named)
