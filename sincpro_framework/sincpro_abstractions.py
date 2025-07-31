from abc import ABC, abstractmethod
from typing import Generic, Type, TypeVar

from pydantic import BaseModel, ConfigDict

TypeDTO = TypeVar("TypeDTO", bound="DataTransferObject")
TypeDTOResponse = TypeVar("TypeDTOResponse", bound="DataTransferObject")

# Additional TypeVars for better dependency injection typing
TFeature = TypeVar("TFeature", bound="Feature")
TApplicationService = TypeVar("TApplicationService", bound="ApplicationService")


class DataTransferObject(BaseModel):
    """
    Abstraction that represent a object that will travel through to any layer
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)


class Bus(ABC):
    log_after_execution: bool = True

    @abstractmethod
    def execute(
        self, dto: TypeDTO, return_type: Type[TypeDTOResponse] | None = None
    ) -> TypeDTOResponse | None:
        """
        Main method to execute a DTO.
        If return_type is provided, returns an instance of return_type.
        Otherwise, returns None.
        """


class Feature(ABC, Generic[TypeDTO, TypeDTOResponse]):
    """
    Feature is the first layer of the framework, it is the main abstraction to execute a business logic.

    Features are atomic operations that handle specific business use cases. They receive a DTO,
    execute business logic, and return a response DTO.

    Features automatically receive injected dependencies as attributes through the framework's
    dependency injection system. These dependencies can be accessed via self.dependency_name.

    For better IDE support with typed dependencies, inherit with specific DTO types:

    Example:
        @framework.feature(MyInputDTO)
        class MyFeature(Feature[MyInputDTO, MyResponseDTO]):
            # Type your injected dependencies for IDE autocomplete
            database_adapter: DatabaseAdapter
            external_service: ExternalService

            def execute(self, dto: MyInputDTO) -> MyResponseDTO:
                # Access context with the new API
                correlation_id = self.context.get("correlation_id")
                user_id = self.context.get("user.id")

                result = self.database_adapter.query(dto.param)
                return MyResponseDTO(result=result)

    For backward compatibility, you can also use untyped Feature:

        @framework.feature(MyInputDTO)
        class MyFeature(Feature):
            def execute(self, dto: MyInputDTO) -> MyResponseDTO:
                # This still works but with less IDE support
                return MyResponseDTO(result="example")
    """

    context: dict

    def __init__(self, *args, **kwargs):
        """
        Initialize the Feature. Dependencies are injected automatically by the framework.
        """
        self.context = {}

    @abstractmethod
    def execute(self, dto: TypeDTO) -> TypeDTOResponse | None:
        """
        Execute the feature's business logic.

        Args:
            dto: The input Data Transfer Object containing request parameters

        Returns:
            A response DTO containing the operation result, or None
        """


class ApplicationService(ABC, Generic[TypeDTO, TypeDTOResponse]):
    """
    Second layer of the framework, orchestration of features.

    ApplicationServices coordinate multiple Features to accomplish complex business workflows.
    They have access to all injected dependencies (same as Features) plus an exclusive
    feature_bus for executing other Features.

    ApplicationServices are ideal for:
    - Non-atomic operations requiring multiple steps
    - Coordinating between different Features
    - Complex business workflows with multiple decision points
    - Aggregating data from multiple sources

    For better IDE support with typed dependencies, inherit with specific DTO types:

    Example:
        @framework.app_service(MyOrchestrationDTO)
        class MyApplicationService(ApplicationService[MyOrchestrationDTO, MyResponseDTO]):
            # Type your injected dependencies for IDE autocomplete
            external_service: ExternalService

            def execute(self, dto: MyOrchestrationDTO) -> MyResponseDTO:
                # Access context with the new API
                correlation_id = self.context.get("correlation_id")
                user_id = self.context.get("user.id")

                # Execute Features through feature_bus with proper typing
                step1_result = self.feature_bus.execute(Step1DTO(...), Step1ResponseDTO)
                step2_result = self.feature_bus.execute(Step2DTO(...), Step2ResponseDTO)

                # Use injected dependencies for additional operations
                final_result = self.external_service.combine(step1_result, step2_result)
                return MyResponseDTO(result=final_result)

    For backward compatibility, you can also use untyped ApplicationService:

        @framework.app_service(MyOrchestrationDTO)
        class MyApplicationService(ApplicationService):
            def execute(self, dto: MyOrchestrationDTO) -> MyResponseDTO:
                # This still works but with less IDE support
                return MyResponseDTO(result="example")
    """

    context: dict
    feature_bus: Bus

    def __init__(self, feature_bus: Bus, *args, **kwargs):
        """
        Initialize the ApplicationService with feature_bus for orchestration.
        Additional dependencies are injected automatically by the framework.
        """
        self.feature_bus = feature_bus
        self.context = {}

    @abstractmethod
    def execute(self, dto: TypeDTO) -> TypeDTOResponse | None:
        """
        Execute the application service orchestration logic.

        Args:
            dto: The input Data Transfer Object containing orchestration parameters

        Returns:
            A response DTO containing the orchestration result, or None
        """
