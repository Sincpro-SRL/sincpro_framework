"""sincpro_framework — a bounded context as a bus of use cases, with what every service needs
around it: context, persistence, events, caching, observability and transports.

    from sincpro_framework import UseFramework, Feature, DataTransferObject

    billing = UseFramework("billing")

    @billing.feature(CommandIssueInvoice)
    class IssueInvoice(Feature):
        def execute(self, dto: CommandIssueInvoice) -> ResponseInvoice: ...

    billing(CommandIssueInvoice(total=100))

    store_context_in(RedisKeyValue(redis))     what the service keeps, shared by its replicas
    @cache.ttl(timedelta(minutes=5))           the service's cache, beside every bus's own

The top level holds what nearly every project imports; each component has its own package
(`sincpro_framework.context`, `.ddd`, `.pipelines`, `.data_layer.caching`, …).
"""

from typing import TYPE_CHECKING, Any

from .bus_pipeline.interceptors import CallNext
from .exceptions import (
    ClientError,
    ExternalServiceError,
    FailureKind,
    FrameworkError,
    OutcomeUnknownError,
    ProgrammingError,
    ServiceUnavailableError,
)
from .sincpro_abstractions import (
    ApplicationService,
    DataTransferObject,
    Feature,
    TypeDTO,
    TypeDTOResponse,
)
from .sincpro_logger import logger
from .store.entrypoint.resolution import store_context_in
from .use_bus import UseFramework

if TYPE_CHECKING:
    from .data_layer.caching.entrypoint.facades import CacheFacade, IdempotencyFacade

    cache: CacheFacade
    """The service's cache, global to every bounded context — kept where the process keeps its
    context (`store_context_in`), else in this process's memory."""
    idempotency: IdempotencyFacade
    """The service's idempotency, global to every bounded context."""


def __getattr__(name: str) -> Any:
    """`cache` and `idempotency` load the caching package when first asked for, never when the
    framework is imported."""
    if name in ("cache", "idempotency"):
        from .data_layer.caching.entrypoint import facades

        return getattr(facades, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ApplicationService",
    "ClientError",
    "CallNext",
    "DataTransferObject",
    "ExternalServiceError",
    "FailureKind",
    "FrameworkError",
    "OutcomeUnknownError",
    "ProgrammingError",
    "ServiceUnavailableError",
    "Feature",
    "UseFramework",
    "cache",
    "idempotency",
    "store_context_in",
    "logger",
    "TypeDTO",
    "TypeDTOResponse",
]
