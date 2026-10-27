"""The buses a `UseFramework` dispatches through: a `FrameworkBus` facade that routes each DTO
to the `FeatureBus` or the `ApplicationServiceBus` that has it registered.

    billing(CommandIssueInvoice(amount=10), ResponseIssueInvoice)   the FrameworkBus routes
    self.feature_bus(CommandReserve(sku="A1"), ResponseReserve)     in an ApplicationService

Built by the container, never by hand outside a test. Every run of a use case goes the same
way, whichever layer it is in: an execution node in the context tree, a span and a measurement
around it, the context providers and `requires_context`, the incomes, the interceptors around
`execute`; on a failure the outcomes, the metric, the span and GlitchTip are told, and the
error handler of that layer may answer instead. The `FrameworkBus` adds what only the
outermost call can do: report the error that leaves the whole call, once.
"""

from logging import Logger
from typing import Any, Callable, Dict, Optional, Type

from sincpro_framework import exceptions
from sincpro_framework.bus_pipeline.incomes.incomes import Incomes
from sincpro_framework.bus_pipeline.interceptors.interceptor import Interceptor, run_around
from sincpro_framework.bus_pipeline.outcomes.outcomes import Outcomes
from sincpro_framework.common.ids import new_entity_id
from sincpro_framework.common.naming import registered_name
from sincpro_framework.context import ContextLevel, ContextProvider, context_executions
from sincpro_framework.observability import Observability
from sincpro_framework.sincpro_abstractions import (
    ApplicationService,
    Bus,
    DataTransferObject,
    DryRunnable,
    Feature,
    TypeDTO,
    TypeDTOResponse,
)

from .sincpro_logger import is_logger_in_debug, logger


def answering(handler: Any, dto: Any) -> Callable[[Any], Any]:
    """What answers `dto` on `handler`: its `preview` when `dto` asks for a dry run and the
    handler has one, its `execute` otherwise.

        answering(CreateQuotation(), CommandCreateQuotation(..., dry_run=True))  ->  .preview
        answering(CreateQuotation(), CommandCreateQuotation(...))                ->  .execute
        answering(<no preview>, CommandCreateQuotation(..., dry_run=True))       ->  .execute
    """
    if isinstance(dto, DryRunnable) and dto.dry_run:
        preview = getattr(handler, "preview", None)
        if callable(preview):
            return preview
    return handler.execute


class FeatureBus(Bus):
    """First layer of the framework, atomic features"""

    def __init__(
        self,
        logger_bus: Logger = logger,  # type: ignore[assignment]
        observability: Optional[Observability] = None,
        incomes: Optional[Incomes] = None,
        outcomes: Optional[Outcomes] = None,
        context_owner: object = None,
        context_providers: Optional[list[ContextProvider]] = None,
        new_execution_id: Optional[Callable[[], str]] = None,
        log_after_execution: bool = True,
    ):
        """Every collaborator optional, so a test builds one bare; the container hands in the
        ones the whole `UseFramework` shares — its observability, incomes, outcomes, context
        owner and providers — so both layers report to the same places. The registry,
        interceptors, replacements and `handle_error` are filled after, by the container.

            FeatureBus()                                    ->  empty registry, no handler
            FeatureBus(observability=Observability("billing"))
            ->  incomes and outcomes of their own, both named "billing"

        1. Empty registry, interceptors and replacements; no `handle_error` yet.
        2. The logger given, else the framework's; the observability given, else a bare one.
        3. The incomes and the outcomes given, else new ones named after the bus.
        4. The execution id factory given, else `new_entity_id`.
        Final: the context owner, the providers (none by default) and whether to log each
        execution stored as given.
        """
        self.feature_registry: Dict[type, Feature] = dict()
        self.interceptors: Dict[type, tuple[Interceptor, ...]] = dict()
        self.replacements: Dict[type, tuple[str, ...]] = dict()
        self.handle_error: Optional[Callable] = None
        self.logger: Logger = logger_bus or logger  # type: ignore[assignment]
        self.observability = observability or Observability()
        self.incomes = (
            incomes if incomes is not None else Incomes(self.observability.bus_name)
        )
        self.outcomes = (
            outcomes if outcomes is not None else Outcomes(self.observability.bus_name)
        )
        self.new_execution_id: Callable[[], str] = new_execution_id or new_entity_id
        self.context_owner: object = context_owner
        self.context_providers: list[ContextProvider] = (
            context_providers if context_providers is not None else []
        )
        self.log_after_execution = log_after_execution

    def register_feature(self, dto: Type[DataTransferObject], feature: Feature) -> bool:
        """Register the feature that answers `dto` — one per DTO type; a second is
        `DTOAlreadyRegistered`. Called by the container, not by a bounded context.

            register_feature(CommandReserve, Reserve())   ->  True
            register_feature(CommandReserve, Other())     ->  DTOAlreadyRegistered

        Logged at info, so the boot log lists every feature the bus answers.
        """
        if dto in self.feature_registry:
            raise exceptions.DTOAlreadyRegistered(
                f"Data transfer object {dto.__name__} is already registered"
            )

        self.logger.info(f"Registering feature [{dto.__name__}]")
        self.feature_registry[dto] = feature
        return True

    def execute(  # type: ignore[override]
        self, dto: TypeDTO, return_type: Type[TypeDTOResponse] | None = None
    ) -> TypeDTOResponse | None:
        """Run the feature registered for the DTO's type, observed and guarded as every one.

        `return_type` only types the answer for the caller — nothing is converted.

            execute(CommandReserve(sku="A1"), ResponseReserve)  ->  ResponseReserve(...)
            execute(CommandUnregistered())                      ->  UnknownDTOToExecute

        1. The feature for `type(dto)`, else `UnknownDTOToExecute`.
        2. Opened together: the execution's node (`ContextLevel.FEATURE`), its span, its measurement.
        3. The span described with what the DTO declares, and with what the feature replaces.
        4. Run, under `handling` so a failure names the chain it crossed:
           4.1 the context providers fill the node; `requires_context` refuses what is
               missing;
           4.2 the incomes are told it began;
           4.3 the interceptors registered for the type run around `feature.execute` — its
               `preview`, for a dry run (`DryRunnable`).
        5. A failure: outcomes, metric and span/GlitchTip told; with no feature error handler
           it is raised, else what the handler answers is the answer — logged as handled.
        Final: the response, measured, told to the outcomes, described on the span.
        """
        dto_type = dto.__class__
        dto_name = dto_type.__name__

        feature = self.feature_registry.get(dto_type)
        if feature is None:
            raise exceptions.UnknownDTOToExecute(f"{dto_name} is not registered as a feature")
        with (
            context_executions.opened(
                dto,
                self.observability.bus_name,
                ContextLevel.FEATURE,
                self.new_execution_id,
                self.context_owner,
            ) as node,
            self.observability.span(
                dto_name,
                "feature",
                registered_name(dto_type, self.observability.bus_name),
            ) as span,
            self.observability.measure(dto, feature, "feature") as measured,
        ):
            self.observability.describe(span, feature, dto)
            if dto_type in self.replacements:
                self.observability.annotate(
                    span, {"sincpro.replaces": ", ".join(self.replacements[dto_type])}
                )
            if is_logger_in_debug() or self.log_after_execution:
                self.logger.info(f"Executing feature dto: [{dto_name}]")
            self.logger.debug(f"{dto_name}({dto})")

            with self.observability.handling(dto_name):
                try:
                    context_executions.prepare(node, feature, self.context_providers)
                    self.incomes.began(dto, "feature", node)
                    chain = self.interceptors.get(dto_type, ())
                    response = run_around(chain, answering(feature, dto), dto)
                except Exception as error:
                    self.outcomes.failed_in(error, dto, "feature", node)
                    measured.failed(error)
                    self.observability.failed(error, dto, feature, "feature", span)
                    if not self.handle_error:
                        raise
                    answer = self.handle_error(error)
                    self.observability.handled(error, dto)
                    return answer

            measured.answered(response)
            self.outcomes.answered(dto, response, "feature", node)
            self.observability.describe(span, feature, response)
            if response:
                self.logger.debug(
                    f"Feature response {response.__class__.__name__}({response})",
                )
            return response

    def __call__(
        self, dto: TypeDTO, return_type: Type[TypeDTOResponse] | None = None
    ) -> TypeDTOResponse | None:
        """`execute`, as a call.

        Context: inside an ApplicationService, `self.feature_bus(dto, Response)` reads as the
        root bus does, `bus(dto, Response)`."""
        return self.execute(dto, return_type)


class ApplicationServiceBus(Bus):
    """Second layer of the framework, orchestration of features
    This object contains the feature bus internally
    """

    def __init__(
        self,
        logger_bus: Logger = logger,  # type: ignore[assignment]
        observability: Optional[Observability] = None,
        incomes: Optional[Incomes] = None,
        outcomes: Optional[Outcomes] = None,
        context_owner: object = None,
        context_providers: Optional[list[ContextProvider]] = None,
        new_execution_id: Optional[Callable[[], str]] = None,
        log_after_execution: bool = True,
    ):
        """As `FeatureBus.__init__` — the same shared collaborators, so a feature and the
        application service that runs it report to the same places.

            ApplicationServiceBus()                         ->  empty registry, no handler
            ApplicationServiceBus(incomes=shared, outcomes=shared)
            ->  told to the same incomes and outcomes as the feature bus

        1. Empty registry, interceptors and replacements; no `handle_error` yet.
        2. The logger given, else the framework's; the observability given, else a bare one.
        3. The incomes and the outcomes given, else new ones named after the bus.
        4. The execution id factory given, else `new_entity_id`.
        Final: the context owner, the providers (none by default) and whether to log each
        execution stored as given.
        """
        self.app_service_registry: Dict[type, ApplicationService] = dict()
        self.interceptors: Dict[type, tuple[Interceptor, ...]] = dict()
        self.replacements: Dict[type, tuple[str, ...]] = dict()
        self.handle_error: Optional[Callable] = None
        self.logger = logger_bus or logger
        self.observability = observability or Observability()
        self.incomes = (
            incomes if incomes is not None else Incomes(self.observability.bus_name)
        )
        self.outcomes = (
            outcomes if outcomes is not None else Outcomes(self.observability.bus_name)
        )
        self.new_execution_id: Callable[[], str] = new_execution_id or new_entity_id
        self.context_owner: object = context_owner
        self.context_providers: list[ContextProvider] = (
            context_providers if context_providers is not None else []
        )
        self.log_after_execution = log_after_execution

    def register_app_service(
        self, dto: Type[DataTransferObject], app_service: ApplicationService
    ) -> bool:
        """Register the application service that answers `dto` — one per DTO type; a second
        is `DTOAlreadyRegistered`. Called by the container when `@bus.app_service(...)` is
        built, not by a bounded context.

            register_app_service(CommandCheckout, Checkout())   ->  True
            register_app_service(CommandCheckout, Other())      ->  DTOAlreadyRegistered

        Logged at info, so the boot log lists every application service the bus answers.
        """
        if dto in self.app_service_registry:
            raise exceptions.DTOAlreadyRegistered(
                f"Data transfer object {dto.__name__} is already registered"
            )

        self.logger.info(f"Registering application service [{dto.__name__}]")
        self.app_service_registry[dto] = app_service
        return True

    def execute(  # type: ignore[override]
        self, dto: TypeDTO, return_type: Type[TypeDTOResponse] | None = None
    ) -> TypeDTOResponse | None:
        """Run the application service registered for the DTO's type — the same way
        `FeatureBus.execute` runs a feature, at `ContextLevel.APPLICATION`, with the application
        service error handler.

            execute(CommandCheckout(cart_id="c1"), ResponseCheckout) ->  ResponseCheckout(...)
            execute(CommandReserve(sku="A1"))   ->  UnknownDTOToExecute (a feature, not here)

        1. The application service for `type(dto)`, else `UnknownDTOToExecute`.
        2. Node, span and measurement opened; the span described.
        3. Providers and `requires_context`, incomes, interceptors around `execute` — around
           `preview`, for a dry run.
        4. A failure: told everywhere; raised, or answered by the error handler.
        Final: the response, measured, told to the outcomes, described on the span.
        """
        dto_type = dto.__class__
        dto_name = dto_type.__name__

        app_service = self.app_service_registry.get(dto_type)
        if app_service is None:
            raise exceptions.UnknownDTOToExecute(
                f"{dto_name} is not registered as an application service"
            )
        with (
            context_executions.opened(
                dto,
                self.observability.bus_name,
                ContextLevel.APPLICATION,
                self.new_execution_id,
                self.context_owner,
            ) as node,
            self.observability.span(
                dto_name,
                "application_service",
                registered_name(dto_type, self.observability.bus_name),
            ) as span,
            self.observability.measure(dto, app_service, "application_service") as measured,
        ):
            self.observability.describe(span, app_service, dto)
            if dto_type in self.replacements:
                self.observability.annotate(
                    span, {"sincpro.replaces": ", ".join(self.replacements[dto_type])}
                )
            if is_logger_in_debug() or self.log_after_execution:
                self.logger.info(f"Executing app service dto: [{dto_name}]")
            self.logger.debug(f"{dto_name}({dto})")

            with self.observability.handling(dto_name):
                try:
                    context_executions.prepare(node, app_service, self.context_providers)
                    self.incomes.began(dto, "application_service", node)
                    chain = self.interceptors.get(dto_type, ())
                    response = run_around(chain, answering(app_service, dto), dto)
                except Exception as error:
                    self.outcomes.failed_in(error, dto, "application_service", node)
                    measured.failed(error)
                    self.observability.failed(
                        error, dto, app_service, "application_service", span
                    )
                    if not self.handle_error:
                        raise
                    answer = self.handle_error(error)
                    self.observability.handled(error, dto)
                    return answer

            measured.answered(response)
            self.outcomes.answered(dto, response, "application_service", node)
            self.observability.describe(span, app_service, response)
            if response:
                self.logger.debug(
                    f"Application service response {response.__class__.__name__}({response})"
                )
            return response


# ---------------------------------------------------------------------------------------------
# Pattern Facade bus
# ---------------------------------------------------------------------------------------------
class FrameworkBus(Bus):
    """Facade bus to orchestrate the feature bus and app service bus
    This component contains the following buses:

    - Feature bus
    - App service bus (This contain the feature bus internally)
    """

    feature_bus: FeatureBus
    app_service_bus: ApplicationServiceBus

    def __init__(
        self,
        feature_bus: FeatureBus,
        app_service_bus: ApplicationServiceBus,
        logger_bus: Logger = logger,  # type: ignore[assignment]
        observability: Optional[Observability] = None,
        outcomes: Optional[Outcomes] = None,
        log_after_execution: bool = True,
    ):
        """The two layer buses, already filled — a DTO registered in both is refused here,
        when the bus is built (`DTOAlreadyRegistered`), not at its first call. `outcomes` is
        the feature bus's unless given, so the error that escapes is told where the rest is.

            FrameworkBus(feature_bus, app_service_bus)      ->  no global error handler yet
            FrameworkBus(<CommandReserve in both buses>)    ->  DTOAlreadyRegistered

        1. Both layer buses stored; no `handle_error` yet.
        2. The logger given, else the framework's; the observability given, else a bare one.
        3. The outcomes given, else the feature bus's.
        4. The DTO types registered in each layer bus, collected.
        Final: a type in both is logged as an error and refused, naming every one of them.
        """
        self.feature_bus = feature_bus
        self.app_service_bus = app_service_bus
        self.handle_error: Optional[Callable] = None
        self.logger = logger_bus or logger
        self.observability = observability or Observability()
        self.outcomes = outcomes if outcomes is not None else feature_bus.outcomes
        self.log_after_execution = log_after_execution

        registered_features = set(self.feature_bus.feature_registry.keys())
        registered_app_services = set(self.app_service_bus.app_service_registry.keys())
        self.logger.debug("Framework bus created")

        intersection_dtos = registered_features.intersection(registered_app_services)
        if intersection_dtos:
            intersection_names = {dto.__name__ for dto in intersection_dtos}
            self.logger.error(
                f"Features and app services have the same DTO: {intersection_names}",
            )
            raise exceptions.DTOAlreadyRegistered(
                f"Data transfer object {intersection_names} is present in application services and features, Change "
                f"the name of the feature or create another framework instance to handle in doupled wat"
            )

    def _dispatch(self, dto: TypeDTO) -> TypeDTOResponse | None:
        """The layer bus that has the DTO's type registered runs it.

        1. Registered in both (one added to a registry after the build):
           `DTOAlreadyRegistered`.
        2. A feature: the feature bus.
        3. An application service: the application service bus.
        Final: neither — `UnknownDTOToExecute`, most often a use case whose decorator never
        ran because its module was never imported.
        """
        dto_type = dto.__class__
        dto_name = dto_type.__name__
        if (
            dto_type in self.app_service_bus.app_service_registry
            and dto_type in self.feature_bus.feature_registry
        ):
            raise exceptions.DTOAlreadyRegistered(
                f"Data transfer object {dto_name} is present in application services and features, Change the "
                f"name of the feature or create another framework instance to handle in doupled wat"
            )
        if dto_type in self.feature_bus.feature_registry:
            return self.feature_bus.execute(dto)

        if dto_type in self.app_service_bus.app_service_registry:
            return self.app_service_bus.execute(dto)

        raise exceptions.UnknownDTOToExecute(
            f"the DTO {dto_name} was not able to execute nothing review if the decorators are used properly, "
            f"otherwise the DTO {dto_name} was never register using the decorator"
        )

    def execute(  # type: ignore[override]
        self, dto: TypeDTO, return_type: Type[TypeDTOResponse] | None = None
    ) -> TypeDTOResponse | None:
        """Run a DTO through the layer bus that has it — what `UseFramework.__call__` ends in.

        Context: the layer buses already reported a failure where it happened; this is the
        last place an error is seen before the caller, so only here, and only when this call
        is the outermost one (not a bus called from inside a use case), is it told as escaped
        — once per call, not once per bus it crossed.

            execute(CommandReserve(sku="A1"))      ->  the feature bus's ResponseReserve
            execute(CommandUnregistered())         ->  UnknownDTOToExecute, told as escaped
            execute(<fails, global handler set>)   ->  what the handler answers

        1. `observability.execution()` says whether this call is the outermost.
        2. Dispatched (`_dispatch`).
        3. A failure:
           3.1 a routing one — unknown DTO, registered twice — reached no layer bus: it is
               recorded here, as a framework error;
           3.2 no global error handler: told as escaped when outermost, and raised;
           3.3 the handler raises: what it raised is told as escaped when outermost, and
               raised.
        Final: what the global handler answered, logged as handled — or the response.
        """
        with self.observability.execution() as outermost:
            try:
                return self._dispatch(dto)
            except Exception as error:
                if isinstance(
                    error, (exceptions.UnknownDTOToExecute, exceptions.DTOAlreadyRegistered)
                ):
                    self.observability.record_error(
                        error, dto.__class__.__name__, "framework", kind="framework"
                    )
                if not self.handle_error:
                    if outermost:
                        self.observability.escaped(error, dto)
                        self.outcomes.escaped(error, dto, self.observability.faulted(error))
                    raise
                try:
                    answer = self.handle_error(error)
                except Exception as raised:
                    if outermost:
                        self.observability.escaped(raised, dto)
                        self.outcomes.escaped(raised, dto, self.observability.faulted(raised))
                    raise
                self.observability.handled(error, dto)
                return answer
