from logging import Logger
from typing import Callable, Dict, Optional, overload

from .exceptions import DTOAlreadyRegistered as DTOAlreadyRegistered
from .exceptions import UnknownDTOToExecute as UnknownDTOToExecute
from .sincpro_abstractions import ApplicationService as ApplicationService
from .sincpro_abstractions import Bus as Bus
from .sincpro_abstractions import DataTransferObject as DataTransferObject
from .sincpro_abstractions import Feature as Feature
from .sincpro_abstractions import TypeDTO as TypeDTO
from .sincpro_abstractions import TypeDTOResponse as TypeDTOResponse
from .sincpro_logger import logger as logger

class FeatureBus(Bus):
    log_after_execution: bool
    feature_registry: Dict[str, Feature]
    handle_error: Optional[Callable]
    logger: Logger

    def __init__(self, logger_bus: Logger = ...) -> None: ...
    def register_feature(self, dto: type[DataTransferObject], feature: Feature) -> bool: ...
    @overload
    def execute(self, dto: TypeDTO) -> None: ...
    @overload
    def execute(
        self, dto: TypeDTO, return_type: type[TypeDTOResponse]
    ) -> TypeDTOResponse: ...

class ApplicationServiceBus(Bus):
    log_after_execution: bool
    app_service_registry: Dict[str, ApplicationService]
    handle_error: Optional[Callable]
    logger: Logger
    def __init__(self, logger_bus: Logger = ...) -> None: ...
    def register_app_service(
        self, dto: type[DataTransferObject], app_service: ApplicationService
    ) -> bool: ...
    @overload
    def execute(self, dto: TypeDTO) -> None: ...
    @overload
    def execute(
        self, dto: TypeDTO, return_type: type[TypeDTOResponse]
    ) -> TypeDTOResponse: ...

class FrameworkBus(Bus):
    log_after_execution: bool
    feature_bus: FeatureBus
    app_service_bus: ApplicationServiceBus
    handle_error: Optional[Callable]
    logger: Logger

    def __init__(
        self,
        feature_bus: FeatureBus,
        app_service_bus: ApplicationServiceBus,
        logger_bus: Logger = ...,
    ) -> None: ...
    @overload
    def execute(self, dto: TypeDTO) -> None: ...
    @overload
    def execute(
        self, dto: TypeDTO, return_type: type[TypeDTOResponse]
    ) -> TypeDTOResponse: ...
