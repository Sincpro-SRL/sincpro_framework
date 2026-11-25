"""One administration of a tenant at a time, across every replica — a lease per tenant (and one
for what every tenant shares), held while a change is checked and written.

    locks = AdministrationLocks()                 # the running context's store
    with locks.held("bo"):
        ...  check the change against what is stored now, then write it

Context: a role checked and assigned while another administrator removes it, or changes its
shape, would leave behind what neither allowed; one lease per tenant makes the check and the
write one step for everybody administering that tenant. Another tenant's administration never
waits. A lease taken is waited for a moment and then refused (`LeaseTaken`, 409 in progress):
the administrator tries again. A holder that dies leaves the lease to run out (`ttl`).
"""

import time
from collections.abc import Callable, Generator
from contextlib import ExitStack, contextmanager
from datetime import timedelta

from sincpro_framework.common.ids import new_entity_id
from sincpro_framework.context.entrypoint.resolution import current_store, key_value_store_of
from sincpro_framework.data_layer.store import KeyValueStore, Leases, LeaseTaken
from sincpro_framework.sincpro_logger import logger


class AdministrationLocks:
    """The leases administrations of auth take, on `store` — the running context's when none."""

    def __init__(
        self,
        store: KeyValueStore | None = None,
        prefix: str = "sincpro:auth:administering",
        ttl: timedelta = timedelta(seconds=30),
        wait: timedelta = timedelta(seconds=2),
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """`wait` is how long a lease another holds is waited for before refusing."""
        self._store = store
        self.prefix = prefix
        self.ttl = ttl
        self.wait = wait
        self.sleep = sleep
        self._warned = False

    def _leases(self) -> Leases:
        """The leases on the store given, else the running context's — said once when that
        store is this process's alone: a change is then one at a time on this replica only."""
        store = (
            self._store if self._store is not None else key_value_store_of(current_store())
        )
        if not self._warned and not store.shared():
            self._warned = True
            logger.warning(
                "auth administration leases are held in this process alone: with more than one "
                "replica, give the store the roles and assignments are kept on (store=...)"
            )
        return Leases(store, prefix=self.prefix)

    @contextmanager
    def held(self, tenant: str | None) -> Generator[None]:
        """The block, holding `tenant`'s lease — `*` for what every tenant shares.

        1. Taken, or tried again every 50 ms for `wait`.
        2. Still taken: `LeaseTaken`.
        Final: the block runs; the lease released when it ends, raised or not.
        """
        name = "*" if tenant is None else tenant
        owner = new_entity_id()
        leases = self._leases()
        deadline = time.monotonic() + self.wait.total_seconds()
        with ExitStack() as holding:
            while True:
                try:
                    holding.enter_context(leases.held(name, owner, self.ttl))
                    break
                except LeaseTaken:
                    if time.monotonic() >= deadline:
                        raise
                    self.sleep(0.05)
            yield
