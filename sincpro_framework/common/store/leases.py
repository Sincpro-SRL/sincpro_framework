"""`Leases`: a claim on a name, held by one owner at a time across every replica, renewed while
its work runs and given up when it ends — or left to run out when its holder dies.

    leases = Leases(store)                                            # any KeyValueStore

    with leases.held("sandbox:S-1", owner="worker-a", ttl=timedelta(minutes=2)) as lease:
        ...                                       renewed in the background every ttl / 3
        lease.check()                             LeaseLost once somebody else may hold it
        write(resource, fence=lease.fence)        a resource refuses a fence older than its last

    leases.state("sandbox:S-1")                   FREE · HELD · EXPIRED

**Why the store, and nothing else.** A lease lasts minutes and crosses processes, so it lives where
every replica reads: the same `KeyValueStore` as the context, the cache, the cron ticks — memory,
Redis, Valkey, Memcached, DynamoDB. It is not a row lock: `for_update` holds a row for one
transaction; a lease holds a name for as long as its holder keeps renewing it.

**What makes it safe.** Taking is an atomic `add` with the lease's time; renewing and releasing are
`replace` — "only if it is still mine" — so a holder whose time passed can neither extend nor free
the lease somebody else took. Each take gets a **fence**, one more than the last holder's: a
holder that wakes up late still carries the old one, and a resource that remembers the last fence
it saw refuses it (the fencing token of distributed locking).

**Expired is not free.** A holder that stops renewing without releasing may still be working — a
long pause, a network partition — so the name is `EXPIRED`, not `FREE`, until somebody takes it
again: what a reclaimer looks for, and what it treats with care. The last holder is remembered
for `remember_for` (a week); a fence starts over after it.

Renewing in the background is a thread; a store that does not answer is retried at the next tick
and the lease's own time decides, so a short outage of the store loses nothing.
"""

import json
import threading
from collections.abc import Generator
from contextlib import contextmanager
from datetime import timedelta
from enum import StrEnum
from typing import Any

from sincpro_framework.common.ids import new_entity_id
from sincpro_framework.common.store import exceptions
from sincpro_framework.common.store.key_value import KeyValueStore
from sincpro_framework.sincpro_logger import logger

TTL = timedelta(minutes=1)
"""How long a lease lives without a renewal, unless the taker says."""
REMEMBER_FOR = timedelta(days=7)
"""How long the last holder of a name is remembered — what tells `EXPIRED` from `FREE`."""


class LeaseState(StrEnum):
    FREE = "free"
    """Never taken, or released by its holder."""
    HELD = "held"
    """Somebody holds it, and has renewed it in time."""
    EXPIRED = "expired"
    """Its holder stopped renewing without releasing it: it may still be working."""


class Lease:
    """A lease taken: who holds it, its fence, and whether it was lost."""

    def __init__(
        self,
        leases: "Leases",
        name: str,
        owner: str,
        fence: int,
        ttl: timedelta,
        claimed: bytes,
    ) -> None:
        self.leases = leases
        self.name = name
        self.owner = owner
        self.fence = fence
        """One more than the last holder's: what a resource compares to refuse an older holder."""
        self.ttl = ttl
        self.claimed = claimed
        """The value the claim was written with — what `replace` compares against."""
        self.lost = threading.Event()
        """Set once a renewal found the lease taken: the work it protects is no longer alone."""

    def check(self) -> None:
        """`LeaseLost` once a renewal found the lease gone — call it between steps of the work."""
        if self.lost.is_set():
            raise exceptions.LeaseLost(self.name, self.owner)

    def renew(self) -> None:
        """Its time starts again — or `LeaseLost`, when it ran out and is no longer this holder's."""
        store = self.leases.store
        if not store.replace(
            self.leases.claim_key(self.name), self.claimed, self.claimed, self.ttl
        ):
            self.lost.set()
            raise exceptions.LeaseLost(self.name, self.owner)
        last = self.leases.last(self.name)
        if last is not None and last.get("fence") == self.fence:
            store.set(
                self.leases.last_key(self.name),
                json.dumps(last).encode(),
                self.leases.remember_for,
            )

    def release(self) -> None:
        """The name is free again — unless it was already lost: then it is left as it is, to its
        new holder."""
        if not self.leases.store.replace(
            self.leases.claim_key(self.name), self.claimed, None
        ):
            self.lost.set()
            return
        self.leases.store.set(
            self.leases.last_key(self.name),
            json.dumps({"owner": self.owner, "fence": self.fence, "released": True}).encode(),
            self.leases.remember_for,
        )


class Leases:
    """The leases of one store, under one prefix."""

    def __init__(
        self,
        store: KeyValueStore,
        prefix: str = "sincpro:lease",
        remember_for: timedelta = REMEMBER_FOR,
    ) -> None:
        self.store = store
        self.prefix = prefix
        self.remember_for = remember_for

    def claim_key(self, name: str) -> str:
        return f"{self.prefix}:{name}"

    def last_key(self, name: str) -> str:
        return f"{self.prefix}:last:{name}"

    def last(self, name: str) -> dict[str, Any] | None:
        """What is remembered of the name's last holder: `owner`, `fence`, `released`."""
        found = self.store.get(self.last_key(name))
        return None if found is None else json.loads(found)

    def take(self, name: str, owner: str = "", ttl: timedelta = TTL) -> Lease | None:
        """The lease, when nobody holds it — free, or expired — else `None`.

        1. The claim is one atomic `add`, with the lease's time: of many racing, one takes it.
        2. Its fence is one more than the last holder's, written by the one that took it.
        Final: the lease, its fence, the value it renews and releases against.
        """
        claimed = json.dumps({"owner": owner, "token": new_entity_id()}).encode()
        if not self.store.add(self.claim_key(name), claimed, ttl):
            return None
        last = self.last(name) or {}
        fence = int(last.get("fence", 0)) + 1
        self.store.set(
            self.last_key(name),
            json.dumps({"owner": owner, "fence": fence, "released": False}).encode(),
            self.remember_for,
        )
        return Lease(self, name, owner, fence, ttl, claimed)

    def holder(self, name: str) -> str | None:
        """Who holds the name now — or, when it expired, who held it last."""
        live = self.store.get(self.claim_key(name))
        if live is not None:
            return json.loads(live).get("owner", "")
        last = self.last(name)
        return None if last is None or last.get("released") else last.get("owner", "")

    def state(self, name: str) -> LeaseState:
        if self.store.get(self.claim_key(name)) is not None:
            return LeaseState.HELD
        last = self.last(name)
        if last is not None and not last.get("released"):
            return LeaseState.EXPIRED
        return LeaseState.FREE

    @contextmanager
    def held(
        self,
        name: str,
        owner: str = "",
        ttl: timedelta = TTL,
        renew_every: timedelta | None = None,
    ) -> Generator[Lease]:
        """The lease for the block, renewed every `renew_every` — a third of its time by default —
        and released at its end; `LeaseTaken` when somebody else holds it.

        A renewal that finds it taken sets `lease.lost` and stops: the block learns it at its next
        `lease.check()`. One the store did not answer is tried again at the next tick."""
        lease = self.take(name, owner, ttl)
        if lease is None:
            raise exceptions.LeaseTaken(name, self.holder(name) or "")
        stopped = threading.Event()
        every = (renew_every or ttl / 3).total_seconds()

        def renewing() -> None:
            while not stopped.wait(every):
                try:
                    lease.renew()
                except exceptions.LeaseLost:
                    return
                except Exception:  # noqa: BLE001 - the lease's own time decides
                    logger.warning(f"lease {name}: a renewal failed; tried again next tick")

        renewer = threading.Thread(target=renewing, name=f"lease:{name}", daemon=True)
        renewer.start()
        try:
            yield lease
        finally:
            stopped.set()
            renewer.join()
            try:
                lease.release()
            except Exception:  # noqa: BLE001 - never hides what the block raised
                logger.warning(f"lease {name}: the release failed; it runs out on its own")
