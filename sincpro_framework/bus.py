from logging import Logger
from typing import Callable, Dict, Optional, Type

from .exceptions import DTOAlreadyRegistered, UnknownDTOToExecute
from .sincpro_abstractions import (
    ApplicationService,
    Bus,
    DataTransferObject,
    Feature,
    TypeDTO,
    TypeDTOResponse,
)
from .sincpro_logger import is_logger_in_debug, logger


class FeatureBus(Bus):
    """First layer of the framework, atomic features"""

    def __init__(self, logger_bus: Logger = logger):
        self.feature_registry: Dict[str, Feature] = dict()
        self.handle_error: Optional[Callable] = None
        self.logger: Logger = logger_bus or logger

    def register_feature(self, dto: Type[DataTransferObject], feature: Feature) -> bool:
        """Register a feature to the bus"""
        if dto.__name__ in self.feature_registry:
            raise DTOAlreadyRegistered(
                f"Data transfer object {dto.__name__} is already registered"
            )

        self.logger.info(f"Registering feature [{dto.__name__}]")
        self.feature_registry[dto.__name__] = feature
        return True

    def execute(
        self, dto: TypeDTO, return_type: Type[TypeDTOResponse] | None = None
    ) -> TypeDTOResponse | None:
        """Execute a feature, and handle error if exists error handler"""
        dto_name = dto.__class__.__name__

        if is_logger_in_debug() or self.log_after_execution:
            self.logger.info(
                f"Executing feature dto: [{dto_name}]",
            )

        self.logger.debug(f"{dto_name}({dto})")

        try:
            response = self.feature_registry[dto.__class__.__name__].execute(dto)
            if response:
                self.logger.debug(
                    f"Feature response {response.__class__.__name__}({response})",
                )
            return response

        except Exception as error:
            if self.handle_error:
                return self.handle_error(error)
            raise error


class ApplicationServiceBus(Bus):
    """Second layer of the framework, orchestration of features
    This object contains the feature bus internally
    """

    def __init__(self, logger_bus: Logger = logger):
        self.app_service_registry: Dict[str, ApplicationService] = dict()
        self.handle_error: Optional[Callable] = None
        self.logger = logger_bus or logger

    def register_app_service(
        self, dto: Type[DataTransferObject], app_service: ApplicationService
    ) -> bool:
        """Register an application service to the bus
        This method is not used directly, the decorator inject_app_service_to_bus is used
        """
        if dto.__name__ in self.app_service_registry:
            raise DTOAlreadyRegistered(
                f"Data transfer object {dto.__name__} is already registered"
            )

        self.logger.info(f"Registering application service [{dto.__name__}]")
        self.app_service_registry[dto.__name__] = app_service
        return True

    def execute(
        self, dto: TypeDTO, return_type: Type[TypeDTOResponse] | None = None
    ) -> TypeDTOResponse | None:
        """Execute an application service, and handle error if exists error handler"""
        dto_name = dto.__class__.__name__
        if is_logger_in_debug() or self.log_after_execution:
            self.logger.info(
                f"Executing app service dto: [{dto_name}]",
            )
        self.logger.debug(f"{dto_name}({dto})")

        try:
            response = self.app_service_registry[dto.__class__.__name__].execute(dto)
            if response:
                self.logger.debug(
                    f"Application service response {response.__class__.__name__}({response})"
                )

            return response

        except Exception as error:
            if self.handle_error:
                return self.handle_error(error)
            raise error


# ---------------------------------------------------------------------------------------------
# Pattern Facade bus
# ---------------------------------------------------------------------------------------------
class FrameworkBus(Bus):
    """Facade bus to orchestrate the feature bus and app service bus
    This component contains the following buses:

    - Feature bus
    - App service bus (This contain the feature bus internally)
    """

    def __init__(
        self,
        feature_bus: FeatureBus,
        app_service_bus: ApplicationServiceBus,
        logger_bus: Logger = logger,
    ):
        self.feature_bus = feature_bus
        self.app_service_bus = app_service_bus
        self.handle_error: Optional[Callable] = None
        self.logger = logger_bus or logger

        registered_features = set(self.feature_bus.feature_registry.keys())
        registered_app_services = set(self.app_service_bus.app_service_registry.keys())
        self.logger.debug("Framework bus created")
        self.logger.debug(
            f"Registered features: {registered_features}",
        )
        self.logger.debug(
            f"Registered app services: {registered_app_services}",
        )

        intersection_dtos = registered_features.intersection(registered_app_services)
        if intersection_dtos:
            self.logger.error(
                f"Features and app services have the same name: {registered_features.intersection(registered_app_services)}",
            )
            raise DTOAlreadyRegistered(
                f"Data transfer object {intersection_dtos} is present in application services and features, Change "
                f"the name of the feature or create another framework instance to handle in doupled wat"
            )

    def execute(
        self, dto: TypeDTO, return_type: Type[TypeDTOResponse] | None = None
    ) -> TypeDTOResponse | None:
        """Main method to execute the framework

        This method will execute the DTO in the app service bus
        if the DTO is present in the app service bus
        otherwise will execute the DTO in the feature bus
        """
        dto_name = dto.__class__.__name__
        try:
            if (
                dto_name in self.app_service_bus.app_service_registry
                and dto_name in self.feature_bus.feature_registry
            ):
                raise DTOAlreadyRegistered(
                    f"Data transfer object {dto_name} is present in application services and features, Change the "
                    f"name of the feature or create another framework instance to handle in doupled wat"
                )
            if dto_name in self.feature_bus.feature_registry:
                response = self.feature_bus.execute(dto)
                return response

            if dto_name in self.app_service_bus.app_service_registry:
                response = self.app_service_bus.execute(dto)
                return response

            raise UnknownDTOToExecute(
                f"the DTO {dto_name} was not able to execute nothing review if the decorators are used properly, "
                f"otherwise the DTO {dto_name} was never register using the decorator"
            )

        except Exception as error:
            if self.handle_error:
                return self.handle_error(error)

            self.logger.exception(f"Error with DTO {dto_name}({dto})")
            raise error
