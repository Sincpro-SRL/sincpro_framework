"""When the context's store does not answer — the context goes on in this process's memory.

    outage_of(store).attempt(lambda: store.version(key), "read the process level")
        ->  the version, or `UNREACHED`: the store did not answer, or failed less than
            `OUTAGE_BYPASS` ago and is not asked again yet

**The store is how processes keep each other informed, never what a call waits on.** Every
read of the context is this process's memory; the store carries what one replica wrote to the
others. When it fails, the context degrades to what this process holds: the shared levels serve
what they read last and keep what they wrote to send it on later, an execution's nodes stay
local, an entrance opens with what the headers carried. A call never fails because the store did
not answer — only what promises to persist (drafts, pipeline runs, idempotency, leases) does.

**One failure stops the asking for a while.** After a failure the store is not asked again for
`OUTAGE_BYPASS`, so a call never waits on a client's timeout and retries while it is down: the
breaker is this process's own (`StoreBypass`, the cache's), never one whose state lives in the
store it guards. Said once when the store stops answering and once when it answers again.
"""

import threading
import time
from collections.abc import Callable
from datetime import timedelta
from enum import Enum
from typing import Final
from weakref import WeakKeyDictionary

from sincpro_framework.context.domain.store import ContextStore
from sincpro_framework.data_layer.store import StoreBypass
from sincpro_framework.sincpro_logger import logger

OUTAGE_BYPASS = timedelta(seconds=5)
"""How long the store is not asked again after it failed — the context is this process's alone
meanwhile."""


class Unreached(Enum):
    """What an attempt answers when the store was not reached — one member, so a type checker
    tells it apart from any answer (`answer is UNREACHED`)."""

    UNREACHED = "unreached"


UNREACHED: Final = Unreached.UNREACHED


class Outage:
    """Whether a store answers, as this process saw it last — and the attempts made on it."""

    __slots__ = ("_name", "_bypass", "_down", "_lock")

    def __init__(self, store: ContextStore) -> None:
        """Answering, until an attempt fails."""
        self._name = type(store).__name__
        self._bypass = StoreBypass(OUTAGE_BYPASS, time.monotonic)
        self._down = False
        self._lock = threading.Lock()

    def attempt[T](self, operation: Callable[[], T], doing: str) -> T | Unreached:
        """What `operation` answers, or `UNREACHED` when the store did not answer.

        1. Failed less than `OUTAGE_BYPASS` ago: not asked — `UNREACHED` at once.
        2. The operation raised: the bypass opens, said once for this outage — `UNREACHED`.
        3. Final: its answer; when the store had stopped answering, said that it is back.

            outage.attempt(lambda: store.load("process:billing"), "read the process level")
                ->  {"lang": "es"}           answering
                ->  UNREACHED                its client raised StoreUnavailable

        Any exception counts: a store of a project's own raises its client's own errors, and the
        call that reached the context must not fail for any of them."""
        if self._bypass.is_open():
            return UNREACHED
        try:
            answer = operation()
        except Exception as error:
            self._bypass.trip()
            with self._lock:
                said, self._down = self._down, True
            if not said:
                logger.warning(
                    f"the context store {self._name} did not answer ({type(error).__name__}: "
                    f"{error}) while trying to {doing}: the context goes on in this process's "
                    f"memory, asked again every {OUTAGE_BYPASS.total_seconds():.0f}s; what it "
                    "writes meanwhile is sent when the store answers"
                )
            return UNREACHED
        if self._down:
            with self._lock:
                said, self._down = self._down, False
            if said:
                logger.info(
                    f"the context store {self._name} answers again: the context is shared again"
                )
        return answer


_outages: WeakKeyDictionary[ContextStore, Outage] = WeakKeyDictionary()
_registering = threading.Lock()


def outage_of(store: ContextStore) -> Outage:
    """The one `Outage` of `store` in this process — every level and node shared through it
    sees the same one, so one failure stops them all from asking."""
    found = _outages.get(store)
    if found is not None:
        return found
    with _registering:
        return _outages.setdefault(store, Outage(store))
