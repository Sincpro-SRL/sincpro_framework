"""`BusCachingMixin`: what a `UseFramework` adds to hold, for its bounded context, a cache and an
idempotency where it stores its context — `billing.cache`, `billing.idempotency`.

    @billing.cache.ttl(timedelta(minutes=5))
    def exchange_rate(currency: str) -> Decimal: ...

    @billing.feature(CommandCharge)
    @billing.idempotency
    class Charge(Feature): ...

    billing.cache.queries(QueryBalance)                               a Query's answer
    billing.add_dependency("siat", SiatClient(cache=billing.cache))    an adapter, injected

Context: the facades write to the bus's store (`bus.store()` — its own, else the process's, else
this process's memory), read at every call, so a store set after the facade was taken is the one
it writes to. Their keys carry the bus's name, so bounded contexts sharing one store never read
each other's.
"""

from typing import TYPE_CHECKING

from sincpro_framework.context import ContextStore
from sincpro_framework.data_layer.caching.entrypoint import facades
from sincpro_framework.data_layer.caching.entrypoint.facades import (
    CacheFacade,
    IdempotencyFacade,
)
from sincpro_framework.data_layer.caching.services.idempotency import Idempotency

if TYPE_CHECKING:
    from sincpro_framework.bus import FrameworkBus


class BusCachingMixin:
    """The bounded context's cache and idempotency, stored on the bus's store."""

    if TYPE_CHECKING:

        @property
        def name(self) -> str:
            """The bus's name — declared for the type checker; `UseFramework` defines it."""
            ...

        def store(self) -> ContextStore:
            """The bus's store — declared for the type checker; `BusStoreMixin` defines it."""
            ...

    @property
    def cache(self) -> CacheFacade:
        """This bounded context's cache — `@billing.cache`, `@billing.cache.ttl(...)`, or handed
        to an adapter.

            billing.cache                   ->  CacheFacade("billing", billing.store, billing)
            billing.cache is billing.cache  ->  True

        Built on the first ask and held on the bus, so every caller shares one facade.
        """
        found = self.__dict__.get("_cache_facade")
        if found is None:
            found = CacheFacade(self.name, self.store, self)
            self.__dict__["_cache_facade"] = found
        return found

    @property
    def idempotency(self) -> IdempotencyFacade:
        """This bounded context's idempotency — `@billing.idempotency` on a Feature or an
        ApplicationService.

            billing.idempotency                         ->  IdempotencyFacade("billing", ...)
            billing.idempotency is billing.idempotency  ->  True

        Built on the first ask and held on the bus, so every caller shares one facade.
        """
        found = self.__dict__.get("_idempotency_facade")
        if found is None:
            found = IdempotencyFacade(self.name, self.store, self)
            self.__dict__["_idempotency_facade"] = found
        return found

    def _idempotency_by_type(self, bus: "FrameworkBus") -> None:
        """Each handler of `bus` whose Command a `declare_for` names runs once per request —
        this bus's declarations first, then the service's.

        1. A class that already runs once — it declared `@billing.idempotency`, or a base of
           it did, or an earlier build gave it its declaration — is left as it is.
        2. Of the declarations whose type the Command is, the nearer in its bases wins; between
           two as near, the first declared.
        Final: that declaration taken by the class, as `@billing.idempotency(...)` would."""
        declared = [*self.idempotency.declared_for, *facades.idempotency.declared_for]
        if not declared:
            return
        handlers = {
            **bus.feature_bus.feature_registry,
            **bus.app_service_bus.app_service_registry,
        }
        for command, handler in handlers.items():
            if Idempotency.declared_by(type(handler)):
                continue
            matching = [one for one in declared if issubclass(command, one[0])]
            if matching:
                nearest = min(matching, key=lambda one: command.__mro__.index(one[0]))
                nearest[1](type(handler))
