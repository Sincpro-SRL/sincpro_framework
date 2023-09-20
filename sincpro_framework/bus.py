from typing import Callable, Optional

from .exceptions import DTOAlreadyRegistered, UnknownDTOToExecute
from .sincpro_abstractions import ApplicationService, Bus, DataTransferObject, Feature
from .sincpro_logger import logger


class FeatureBus(Bus):
    def __init__(self):
        self.feature_registry = dict()
        self.handle_error: Optional[Callable] = None

    def register_feature(self, dto: DataTransferObject, feature: Feature) -> bool:
        if dto.__name__ in self.feature_registry:
            raise DTOAlreadyRegistered(
                f"Data transfer object {dto.__name__} is already registered"
            )

        logger.info("Registering feature")
        self.feature_registry[dto.__name__] = feature
        return True

    def execute(self, dto: DataTransferObject) -> DataTransferObject:
        logger.info(f"Executing feature dto: [{dto.__class__.__name__}]")
        logger.debug(f"{dto}")

        try:
            response = self.feature_registry[dto.__class__.__name__].execute(dto)
            logger.debug(f"{response}")
            return response

        except Exception as error:
            if self.handle_error:
                return self.handle_error(error)

            logger.error(str(error), exc_info=True)
            raise error


class ApplicationServiceBus(Bus):
    def __init__(self):
        self.app_service_registry = dict()
        self.handle_error: Optional[Callable] = None

    def register_app_service(
        self, dto: DataTransferObject, app_service: ApplicationService
    ) -> bool:
        if dto.__name__ in self.app_service_registry:
            raise DTOAlreadyRegistered(
                f"Data transfer object {dto.__name__} is already registered"
            )

        logger.info("Registering application service")
        self.app_service_registry[dto.__name__] = app_service
        return True

    def execute(self, dto: DataTransferObject) -> DataTransferObject:
        logger.info(f"Executing app service dto: [{dto.__class__.__name__}]")
        logger.debug(f"{dto}")
        try:
            response = self.app_service_registry[dto.__class__.__name__].execute(dto)
            logger.debug(f"{response}")
            return response

        except Exception as error:
            if self.handle_error:
                return self.handle_error(error)

            logger.error(str(error), exc_info=True)
            raise error


# ---------------------------------------------------------------------------------------------
# Pattern Facade bus
# ---------------------------------------------------------------------------------------------
class FrameworkBus(Bus):
    def __init__(self, feature_bus: FeatureBus, app_service_bus: ApplicationServiceBus):
        self.feature_bus = feature_bus
        self.app_service_bus = app_service_bus
        self.handle_error: Optional[Callable] = None

        registered_features = set(self.feature_bus.feature_registry.keys())
        registered_app_services = set(self.app_service_bus.app_service_registry.keys())
        logger.debug("Framework bus created")
        logger.debug(f"Registered features: {registered_features}")
        logger.debug(f"Registered app services: {registered_app_services}")

        intersection_dtos = registered_features.intersection(registered_app_services)
        if intersection_dtos:
            logger.error(
                f"Features and app services have the same name: {registered_features.intersection(registered_app_services)}"
            )
            raise DTOAlreadyRegistered(
                f"Data transfer object {intersection_dtos} is present in application services and features, Change "
                f"the name of the feature or create another framework instance to handle in doupled wat"
            )

    def execute(self, dto: DataTransferObject) -> DataTransferObject:
        try:
            dto_name = dto.__class__.__name__

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

            logger.error(str(error), exc_info=True)
            raise error
