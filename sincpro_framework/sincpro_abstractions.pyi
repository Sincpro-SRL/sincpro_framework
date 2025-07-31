import abc
from abc import ABC, abstractmethod
from typing import Any, Generic, Type, TypeVar, overload

from pydantic import BaseModel

class DataTransferObject(BaseModel): ...

TypeDTO = TypeVar("TypeDTO", bound="DataTransferObject")
TypeDTOResponse = TypeVar("TypeDTOResponse", bound="DataTransferObject")

# Additional TypeVars for better dependency injection typing
TFeature = TypeVar("TFeature", bound="Feature")
TApplicationService = TypeVar("TApplicationService", bound="ApplicationService")

class Bus(ABC, metaclass=abc.ABCMeta):
    log_after_execution: bool

    @abstractmethod
    @overload
    def execute(
        self, dto: TypeDTO, return_type: Type[TypeDTOResponse]
    ) -> TypeDTOResponse: ...
    @abstractmethod
    @overload
    def execute(self, dto: TypeDTO) -> TypeDTOResponse | None: ...

class Feature(ABC, Generic[TypeDTO, TypeDTOResponse], metaclass=abc.ABCMeta):
    """
    Feature is the first layer of the framework, it is the main abstraction to execute a business logic.

    Features automatically receive injected dependencies as attributes through the framework's
    dependency injection system. These dependencies can be accessed via self.dependency_name.

    For better IDE support with typed dependencies, inherit with specific DTO types:

        class MyFeature(Feature[MyInputDTO, MyResponseDTO]):
            # Type your injected dependencies for IDE autocomplete
            database_adapter: DatabaseAdapter

            def execute(self, dto: MyInputDTO) -> MyResponseDTO: ...
    """

    context: dict

    def __init__(self, *args, **kwargs) -> None: ...
    @abstractmethod
    def execute(self, dto: TypeDTO) -> TypeDTOResponse | None: ...

    # Note: Injected dependencies become available as attributes at runtime
    # The actual dependency attributes are added dynamically by the framework
    # Custom Feature classes should define typed attributes for IDE support
    def __getattr__(self, name: str) -> Any: ...

class ApplicationService(ABC, Generic[TypeDTO, TypeDTOResponse], metaclass=abc.ABCMeta):
    """
    Second layer of the framework, orchestration of features.

    ApplicationServices have access to all injected dependencies (same as Features)
    plus an exclusive feature_bus for executing other Features.

    For better IDE support with typed dependencies, inherit with specific DTO types:

        class MyService(ApplicationService[MyInputDTO, MyResponseDTO]):
            # Type your injected dependencies for IDE autocomplete
            external_service: ExternalService

            def execute(self, dto: MyInputDTO) -> MyResponseDTO: ...
    """

    context: dict
    feature_bus: Bus
    def __init__(self, feature_bus: Bus, *args, **kwargs) -> None: ...
    @abstractmethod
    def execute(self, dto: TypeDTO) -> TypeDTOResponse | None: ...

    # Note: Injected dependencies become available as attributes at runtime
    # The actual dependency attributes are added dynamically by the framework
    # Custom ApplicationService classes should define typed attributes for IDE support
    def __getattr__(self, name: str) -> Any: ...
