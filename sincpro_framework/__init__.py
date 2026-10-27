"""sincpro_framework — a bounded context as a bus of use cases, with what every service needs
around it: context, persistence, events, caching, observability and transports.

    from sincpro_framework import UseFramework, Feature, DataTransferObject

    billing = UseFramework("billing")

    @billing.feature(CommandIssueInvoice)
    class IssueInvoice(Feature):
        def execute(self, dto: CommandIssueInvoice) -> ResponseInvoice: ...

    billing(CommandIssueInvoice(total=100))

    @cache.ttl(timedelta(minutes=5))           the service's cache, beside every bus's own

    problem_of(error)                          a failure as every wire tells it — kind, reason,
                                               status, what to do next — for a route of one's own

The top level holds what nearly every project imports; each component has its own package
(`sincpro_framework.context` — where `store_context_in` lives —, `.ddd`, `.pipelines`,
`.data_layer.store`, `.data_layer.caching`, …).
"""

from .bus_pipeline.interceptors import CallNext, Interceptor
from .common.failures import NextStep, Problem, problem_of
from .data_layer.caching.entrypoint.facades import cache, idempotency
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
    DryRunnable,
    Feature,
    Preview,
    TypeDTO,
    TypeDTOResponse,
)
from .sincpro_logger import logger
from .use_bus import UseFramework

__all__ = [
    "ApplicationService",
    "ClientError",
    "CallNext",
    "DataTransferObject",
    "DryRunnable",
    "ExternalServiceError",
    "FailureKind",
    "FrameworkError",
    "Interceptor",
    "NextStep",
    "OutcomeUnknownError",
    "Preview",
    "Problem",
    "ProgrammingError",
    "ServiceUnavailableError",
    "Feature",
    "UseFramework",
    "cache",
    "idempotency",
    "logger",
    "problem_of",
    "TypeDTO",
    "TypeDTOResponse",
]
