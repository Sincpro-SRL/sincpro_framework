"""`BusStoreMixin`: what a `UseFramework` adds to say where its bounded context saves what it
saves — `billing.store()`.

    billing.store_context_in(KeyValueContexts(RedisKeyValue(redis)))   its own store
    billing.store()      ->  that store, else the process's (`store_context_in(...)`), else memory

Context: every component that saves something for a bus — its cache and idempotency
(`bus.cache`, `bus.idempotency`), its drafts, its pipeline runs, its leases — asks `bus.store()`
at every call, so a store set after a component was wired is the one it saves on.
"""

from typing import TYPE_CHECKING, Any

from sincpro_framework.context.domain.node import Values
from sincpro_framework.context.domain.store import ContextStore
from sincpro_framework.context.entrypoint.resolution import process_store


class BusStoreMixin:
    """Where the bounded context saves what it saves: its own store, else the process's."""

    _published: Values

    if TYPE_CHECKING:

        @property
        def name(self) -> str:
            """The bus's name — declared for the type checker; `UseFramework` defines it."""
            ...

    def store(self) -> ContextStore:
        """Where this bounded context saves what it saves: its own store, else the process's,
        else this process's memory."""
        own: Any = self._published.current.get(ContextStore)
        return own if isinstance(own, ContextStore) else process_store()
