"""One administration of a tenant at a time, across every replica — a lease per tenant (and one
for what every tenant shares), held while a change is checked and written.

    locks = AdministrationLocks()                 # the running context's store
    with locks.acquire("bo"):
        ...  check the change against what is stored now, then write it

Context: a role checked and assigned while another administrator removes it, or changes its
shape, would leave behind what neither allowed; one lease per tenant makes the check and the
write one step for everybody administering that tenant. Another tenant's administration never
waits. A lease taken is waited for a moment and then refused (`LeaseTaken`, 409 in progress):
the administrator tries again. A holder that dies leaves the lease to run out (`ttl`). A store
resolved from the context that lives in this process alone is warned about once.
"""

import time
from collections.abc import Callable, Generator
from contextlib import ExitStack, contextmanager
from datetime import timedelta

from sincpro_framework.auth.common.adapters.key_value import KeyValueStoreMixin
from sincpro_framework.common.ids import new_entity_id
from sincpro_framework.data_layer.store import KeyValueStore, Leases, LeaseTaken


class AdministrationLocks(KeyValueStoreMixin):
    """The leases administrations of auth take, on `store` — the running context's when none."""

    warn_when_unshared = True
    """A lease held in this process alone serializes the administrations of this replica only."""

    def __init__(
        self,
        store: KeyValueStore | None = None,
        prefix: str = "sincpro:auth:administering",
        ttl: timedelta = timedelta(seconds=30),
        wait_timeout: timedelta = timedelta(seconds=2),
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """`wait_timeout` bounds how long a lease another holds is waited for before refusing."""
        self._init_key_value_store(store, prefix)
        self.ttl = ttl
        self.wait_timeout = wait_timeout
        self.sleep = sleep

    @contextmanager
    def acquire(self, tenant: str | None) -> Generator[None]:
        """The block, holding `tenant`'s lease — `*` for what every tenant shares.

        1. Taken, or tried again every 50 ms for `wait_timeout`.
        2. Still taken: `LeaseTaken`.
        Final: the block runs; the lease released when it ends, raised or not.
        """
        name = "*" if tenant is None else tenant
        owner = new_entity_id()
        leases = Leases(self.store(), prefix=self.prefix)
        deadline = time.monotonic() + self.wait_timeout.total_seconds()
        with ExitStack() as acquired:
            while True:
                try:
                    acquired.enter_context(leases.held(name, owner, self.ttl))
                    break
                except LeaseTaken:
                    if time.monotonic() >= deadline:
                        raise
                    self.sleep(0.05)
            yield
