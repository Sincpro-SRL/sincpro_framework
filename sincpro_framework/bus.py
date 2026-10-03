from logging import Logger
from typing import Callable, Dict, Optional, Type

from .context.domain.level import Level
from .context.domain.node import ContextNode
from .context.infrastructure.providers import (
    ContextProvider,
    missing,
    provided,
    required_by,
)
from .context.infrastructure.tree import opened_execution
from .exceptions import ContextRequired, DTOAlreadyRegistered, UnknownDTOToExecute
from .ids import new_entity_id
from .interceptors import Interceptor, run_through
from .observability import Observability
from .sincpro_abstractions import (
    ApplicationService,
    Bus,
    DataTransferObject,
    Feature,
    TypeDTO,
    TypeDTOResponse,
)
from .sincpro_logger import is_logger_in_debug, logger


def prepared(node: ContextNode, handler: object, providers: list[ContextProvider]) -> None:
    """1. What the bus's providers give, on the execution's node.
    2. Final: what the handler declared it requires — refused when missing; nothing declared,
       nothing checked."""
    provided(node, providers)
    required = required_by(handler)
    if required:
        absent = missing(node, required)
        if absent:
            raise ContextRequired(type(handler).__name__, absent)


class FeatureBus(Bus):
    """First layer of the framework, atomic features"""

    def __init__(
        self,
        logger_bus: Logger = logger,  # type: ignore[assignment]
        observability: Optional[Observability] = None,
    ):
        self.feature_registry: Dict[type, Feature] = dict()
        self.interceptors: Dict[type, tuple[Interceptor, ...]] = dict()
        self.replacements: Dict[type, tuple[str, ...]] = dict()
        self.handle_error: Optional[Callable] = None
        self.logger: Logger = logger_bus or logger  # type: ignore[assignment]
        self.observability = observability or Observability()
        self.new_execution_id: Callable[[], str] = new_entity_id
        self.context_owner: object = None
        self.context_providers: list[ContextProvider] = []

    def register_feature(self, dto: Type[DataTransferObject], feature: Feature) -> bool:
        """Register a feature to the bus"""
        if dto in self.feature_registry:
            raise DTOAlreadyRegistered(
                f"Data transfer object {dto.__name__} is already registered"
            )

        self.logger.info(f"Registering feature [{dto.__name__}]")
        self.feature_registry[dto] = feature
        return True

    def execute(  # type: ignore[override]
        self, dto: TypeDTO, return_type: Type[TypeDTOResponse] | None = None
    ) -> TypeDTOResponse | None:
        """Execute a feature, and handle error if exists error handler"""
        dto_type = dto.__class__
        dto_name = dto_type.__name__

        feature = self.feature_registry.get(dto_type)
        if feature is None:
            raise UnknownDTOToExecute(f"{dto_name} is not registered as a feature")
        with (
            opened_execution(
                dto,
                self.observability.bus_name,
                Level.FEATURE,
                self.new_execution_id,
                self.context_owner,
            ) as node,
            self.observability.span(dto_name, "feature") as span,
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
                    prepared(node, feature, self.context_providers)
                    chain = self.interceptors.get(dto_type, ())
                    response = run_through(chain, feature.execute, dto)
                except Exception as error:
                    measured.failed(error)
                    self.observability.failed(error, dto, feature, "feature", span)
                    if not self.handle_error:
                        raise
                    answer = self.handle_error(error)
                    self.observability.handled(error, dto)
                    return answer

            measured.answered(response)
            self.observability.describe(span, feature, response)
            if response:
                self.logger.debug(
                    f"Feature response {response.__class__.__name__}({response})",
                )
            return response

    def __call__(
        self, dto: TypeDTO, return_type: Type[TypeDTOResponse] | None = None
    ) -> TypeDTOResponse | None:
        """Context: inside an ApplicationService, `self.feature_bus(dto, Response)` reads as the
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
    ):
        self.app_service_registry: Dict[type, ApplicationService] = dict()
        self.interceptors: Dict[type, tuple[Interceptor, ...]] = dict()
        self.replacements: Dict[type, tuple[str, ...]] = dict()
        self.handle_error: Optional[Callable] = None
        self.logger = logger_bus or logger
        self.observability = observability or Observability()
        self.new_execution_id: Callable[[], str] = new_entity_id
        self.context_owner: object = None
        self.context_providers: list[ContextProvider] = []

    def register_app_service(
        self, dto: Type[DataTransferObject], app_service: ApplicationService
    ) -> bool:
        """Register an application service to the bus
        This method is not used directly, the decorator inject_app_service_to_bus is used
        """
        if dto in self.app_service_registry:
            raise DTOAlreadyRegistered(
                f"Data transfer object {dto.__name__} is already registered"
            )

        self.logger.info(f"Registering application service [{dto.__name__}]")
        self.app_service_registry[dto] = app_service
        return True

    def execute(  # type: ignore[override]
        self, dto: TypeDTO, return_type: Type[TypeDTOResponse] | None = None
    ) -> TypeDTOResponse | None:
        """Execute an application service, and handle error if exists error handler"""
        dto_type = dto.__class__
        dto_name = dto_type.__name__

        app_service = self.app_service_registry.get(dto_type)
        if app_service is None:
            raise UnknownDTOToExecute(
                f"{dto_name} is not registered as an application service"
            )
        with (
            opened_execution(
                dto,
                self.observability.bus_name,
                Level.APPLICATION,
                self.new_execution_id,
                self.context_owner,
            ) as node,
            self.observability.span(dto_name, "application_service") as span,
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
                    prepared(node, app_service, self.context_providers)
                    chain = self.interceptors.get(dto_type, ())
                    response = run_through(chain, app_service.execute, dto)
                except Exception as error:
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
    ):
        self.feature_bus = feature_bus
        self.app_service_bus = app_service_bus
        self.handle_error: Optional[Callable] = None
        self.logger = logger_bus or logger
        self.observability = observability or Observability()

        registered_features = set(self.feature_bus.feature_registry.keys())
        registered_app_services = set(self.app_service_bus.app_service_registry.keys())
        self.logger.debug("Framework bus created")

        intersection_dtos = registered_features.intersection(registered_app_services)
        if intersection_dtos:
            intersection_names = {dto.__name__ for dto in intersection_dtos}
            self.logger.error(
                f"Features and app services have the same DTO: {intersection_names}",
            )
            raise DTOAlreadyRegistered(
                f"Data transfer object {intersection_names} is present in application services and features, Change "
                f"the name of the feature or create another framework instance to handle in doupled wat"
            )

    def _dispatch(self, dto: TypeDTO) -> TypeDTOResponse | None:
        dto_type = dto.__class__
        dto_name = dto_type.__name__
        if (
            dto_type in self.app_service_bus.app_service_registry
            and dto_type in self.feature_bus.feature_registry
        ):
            raise DTOAlreadyRegistered(
                f"Data transfer object {dto_name} is present in application services and features, Change the "
                f"name of the feature or create another framework instance to handle in doupled wat"
            )
        if dto_type in self.feature_bus.feature_registry:
            return self.feature_bus.execute(dto)

        if dto_type in self.app_service_bus.app_service_registry:
            return self.app_service_bus.execute(dto)

        raise UnknownDTOToExecute(
            f"the DTO {dto_name} was not able to execute nothing review if the decorators are used properly, "
            f"otherwise the DTO {dto_name} was never register using the decorator"
        )

    def execute(  # type: ignore[override]
        self, dto: TypeDTO, return_type: Type[TypeDTOResponse] | None = None
    ) -> TypeDTOResponse | None:
        """Main method to execute the framework

        This method will execute the DTO in the app service bus
        if the DTO is present in the app service bus
        otherwise will execute the DTO in the feature bus
        """
        with self.observability.execution() as outermost:
            try:
                return self._dispatch(dto)
            except Exception as error:
                if isinstance(error, (UnknownDTOToExecute, DTOAlreadyRegistered)):
                    self.observability.record_error(
                        error, dto.__class__.__name__, "framework", kind="framework"
                    )
                if not self.handle_error:
                    if outermost:
                        self.observability.escaped(error, dto)
                    raise
                try:
                    answer = self.handle_error(error)
                except Exception as raised:
                    if outermost:
                        self.observability.escaped(raised, dto)
                    raise
                self.observability.handled(error, dto)
                return answer
