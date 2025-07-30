from abc import ABC, abstractmethod
from typing import Any, Type, TypeVar, Generic

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


class Feature(ABC):
    """
    Feature is the first layer of the framework, it is the main abstraction to execute a business logic.
    
    Features are atomic operations that handle specific business use cases. They receive a DTO,
    execute business logic, and return a response DTO.
    
    Features automatically receive injected dependencies as attributes through the framework's
    dependency injection system. These dependencies can be accessed via self.dependency_name.
    
    Example:
        @framework.feature(MyInputDTO)
        class MyFeature(Feature):
            # Injected dependencies are available as attributes
            # self.database_adapter: DatabaseAdapter
            # self.external_service: ExternalService
            
            def execute(self, dto: MyInputDTO) -> MyResponseDTO:
                result = self.database_adapter.query(dto.param)
                return MyResponseDTO(result=result)
    """

    def __init__(self, *args, **kwargs):
        """
        Initialize the Feature. Dependencies are injected automatically by the framework.
        """
        pass

    @abstractmethod
    def execute(self, dto: TypeDTO) -> TypeDTOResponse | None:
        """
        Execute the feature's business logic.
        
        Args:
            dto: The input Data Transfer Object containing request parameters
            
        Returns:
            A response DTO containing the operation result, or None
        """
        pass


class ApplicationService(ABC):
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
    
    Example:
        @framework.app_service(MyOrchestrationDTO)
        class MyApplicationService(ApplicationService):
            # All Feature dependencies PLUS:
            # self.feature_bus: Bus  # For executing Features
            
            def execute(self, dto: MyOrchestrationDTO) -> MyResponseDTO:
                # Execute Features through feature_bus
                step1_result = self.feature_bus.execute(Step1DTO(...))
                step2_result = self.feature_bus.execute(Step2DTO(...))
                
                # Use injected dependencies for additional operations
                final_result = self.external_service.combine(step1_result, step2_result)
                return MyResponseDTO(result=final_result)
    """

    feature_bus: Bus

    def __init__(self, feature_bus: Bus, *args, **kwargs):
        """
        Initialize the ApplicationService with feature_bus for orchestration.
        Additional dependencies are injected automatically by the framework.
        """
        self.feature_bus = feature_bus

    @abstractmethod
    def execute(self, dto: TypeDTO) -> TypeDTOResponse | None:
        """
        Execute the application service orchestration logic.
        
        Args:
            dto: The input Data Transfer Object containing orchestration parameters
            
        Returns:
            A response DTO containing the orchestration result, or None
        """
        pass
