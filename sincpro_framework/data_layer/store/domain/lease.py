"""`Lease` and `LeaseState`: a claim on a name taken by `Leases`, and where a name stands.

    lease = leases.take("sandbox:S-1", owner="worker-a")    Lease(fence=4) — or None, held
    lease.check()                                           LeaseLost once somebody else may hold it
    lease.renew(); lease.release()

A lease is written on a `KeyValueStore` under two keys: the live claim, with the lease's time as
its expiry, and the last holder, which outlives the claim so `EXPIRED` is told from `FREE` and
the next fence counted on.
"""

import json
import threading
from datetime import timedelta
from enum import StrEnum

from sincpro_framework.data_layer.store.domain import exceptions
from sincpro_framework.data_layer.store.domain.key_value import KeyValueStore


class LeaseState(StrEnum):
    """Where a name stands, as `Leases.state` reads it — three: expired is not free."""

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
        store: KeyValueStore,
        name: str,
        owner: str,
        fence: int,
        ttl: timedelta,
        claimed: bytes,
        claim_key: str,
        last_key: str,
        remember_for: timedelta,
    ) -> None:
        """Built by `Leases.take` once the claim is written — never by hand: `claimed` must be
        the exact bytes in the store, or every renewal reads as lost.

            leases.take("sandbox:S-1", owner="worker-a", ttl=timedelta(minutes=2))
                ->  Lease(name="sandbox:S-1", owner="worker-a", fence=4, ttl=2 min,
                          claimed=b'{"owner": "worker-a", "token": "01J…"}',
                          claim_key="sincpro:lease:sandbox:S-1",
                          last_key="sincpro:lease:last:sandbox:S-1")

        It starts not lost: `lost` is set only by a renewal or a release that found it
        taken."""
        self.store = store
        self.name = name
        self.owner = owner
        self.fence = fence
        """One more than the last holder's: what a resource compares to refuse an older holder."""
        self.ttl = ttl
        self.claimed = claimed
        """The value the claim was written with — what `replace` compares against."""
        self.claim_key = claim_key
        self.last_key = last_key
        self.remember_for = remember_for
        """How long the last holder is remembered after a renewal or a release."""
        self.lost = threading.Event()
        """Set once a renewal found the lease taken: the work it protects is no longer alone."""

    def check(self) -> None:
        """`LeaseLost` once a renewal found the lease gone — call it between steps of the work."""
        if self.lost.is_set():
            raise exceptions.LeaseLost(self.name, self.owner)

    def renew(self) -> None:
        """Its time starts again — or `LeaseLost`, when it ran out and is no longer this holder's.

            lease.renew()   ->  None: the claim stored for another `ttl`
            lease.renew()   ->  LeaseLost("sandbox:S-1", "worker-a"), `lease.lost` set
                                # it ran out and worker-b took it

        The memory of the last holder is stored for `remember_for` again, while it still names
        this lease's fence."""
        if not self.store.replace(self.claim_key, self.claimed, self.claimed, self.ttl):
            self.lost.set()
            raise exceptions.LeaseLost(self.name, self.owner)
        found = self.store.get(self.last_key)
        last = None if found is None else json.loads(found)
        if last is not None and last.get("fence") == self.fence:
            self.store.set(self.last_key, json.dumps(last).encode(), self.remember_for)

    def release(self) -> None:
        """The name is free again — unless it was already lost: then it is left as it is, to its
        new holder."""
        if not self.store.replace(self.claim_key, self.claimed, None):
            self.lost.set()
            return
        self.store.set(
            self.last_key,
            json.dumps({"owner": self.owner, "fence": self.fence, "released": True}).encode(),
            self.remember_for,
        )
