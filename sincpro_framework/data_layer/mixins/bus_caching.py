"""`BusCachingMixin`: what a `UseFramework` adds to keep, for its bounded context, a cache and an
idempotency where it keeps what it keeps — `billing.cache`, `billing.idempotency`.

    @billing.cache.ttl(timedelta(minutes=5))
    def exchange_rate(currency: str) -> Decimal: ...

    @billing.feature(CommandCharge)
    @billing.idempotency
    class Charge(Feature): ...

    billing.cache.queries(QueryBalance)                               a Query's answer
    billing.add_dependency("siat", SiatClient(cache=billing.cache))    an adapter, injected

Context: the facades keep on the bus's store (`bus.store()` — its own, else the process's, else
this process's memory), read at every call, so a store set after the facade was taken is the one
it keeps on. Their keys carry the bus's name, so bounded contexts sharing one store never read
each other's. The facades are loaded when first asked for: the bus never imports them at import
time — and it lives outside `caching/` so mixing it into the bus loads none of that package.
"""

from typing import TYPE_CHECKING

from sincpro_framework.store.domain.store import ContextStore

if TYPE_CHECKING:
    from sincpro_framework.data_layer.caching.entrypoint.facades import (
        CacheFacade,
        IdempotencyFacade,
    )


class BusCachingMixin:
    """The bounded context's cache and idempotency, kept on the bus's store."""

    if TYPE_CHECKING:

        @property
        def name(self) -> str:
            """The bus's name — declared for the type checker; `UseFramework` defines it."""
            ...

        def store(self) -> ContextStore:
            """The bus's store — declared for the type checker; `BusStoreMixin` defines it."""
            ...

    @property
    def cache(self) -> "CacheFacade":
        """This bounded context's cache — `@billing.cache`, `@billing.cache.ttl(...)`, or handed
        to an adapter.

            billing.cache                   ->  CacheFacade("billing", billing.store, billing)
            billing.cache is billing.cache  ->  True

        Built on the first ask and kept on the bus, so every caller shares one facade.
        """
        found = self.__dict__.get("_cache_facade")
        if found is None:
            from sincpro_framework.data_layer.caching.entrypoint.facades import CacheFacade

            found = CacheFacade(self.name, self.store, self)
            self.__dict__["_cache_facade"] = found
        return found

    @property
    def idempotency(self) -> "IdempotencyFacade":
        """This bounded context's idempotency — `@billing.idempotency` on a Feature or an
        ApplicationService.

            billing.idempotency                         ->  IdempotencyFacade("billing", ...)
            billing.idempotency is billing.idempotency  ->  True

        Built on the first ask and kept on the bus, so every caller shares one facade.
        """
        found = self.__dict__.get("_idempotency_facade")
        if found is None:
            from sincpro_framework.data_layer.caching.entrypoint.facades import (
                IdempotencyFacade,
            )

            found = IdempotencyFacade(self.name, self.store)
            self.__dict__["_idempotency_facade"] = found
        return found
