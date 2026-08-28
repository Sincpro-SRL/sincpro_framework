from abc import ABC, abstractmethod
from contextvars import copy_context
from typing import Generic, Type

from pydantic import BaseModel, ConfigDict
from typing_extensions import TypeVar

from .context.framework_context_consumer import ContextConsumer
from .context.thread_context_bus import ThreadContextBus as ThreadContextBus

TypeDTO = TypeVar("TypeDTO", bound="DataTransferObject")
TypeDTOResponse = TypeVar("TypeDTOResponse", bound="DataTransferObject")
ContextT = TypeVar("ContextT")

# Additional TypeVars for better dependency injection typing
TFeature = TypeVar("TFeature", bound="Feature")
TApplicationService = TypeVar("TApplicationService", bound="ApplicationService")


class DataTransferObject(BaseModel):
    """
    Abstraction that represent a object that will travel through to any layer
    """

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        use_attribute_docstrings=True,
    )


class Bus(ABC):
    log_after_execution: bool = True
    service_name: str = ""

    @abstractmethod
    def execute(
        self, dto: TypeDTO, return_type: Type[TypeDTOResponse] | None = None
    ) -> TypeDTOResponse | None:
        """
        Main method to execute a DTO.
        If return_type is provided, returns an instance of return_type.
        Otherwise, returns None.
        """

    def thread_context(self) -> "ThreadContextBus":
        """Return a handle to this bus bound to the calling thread's current context.

        `context()` overlays live in a ``ContextVar``, which is isolated per OS
        thread by design. A plain ``executor.submit(bus.execute, dto)`` runs
        ``execute`` in a *new* thread that never saw the overlay's ``set()``, so
        every Feature's ``self.context`` there silently falls back to the (usually
        empty) shared context. Call ``thread_context()`` here, in the thread that
        still has the overlay active, then hand `.execute` (not the raw bus) to
        the executor.

        Call it **once per task you submit**, not once for a whole batch — a
        captured snapshot can only be entered by one thread at a time, so sharing
        a single one across concurrent workers raises a ``RuntimeError``::

            with ThreadPoolExecutor(max_workers=3) as executor:
                futures = [
                    executor.submit(self.feature_bus.thread_context().execute, dto, return_type)
                    for dto in dtos
                ]
        """
        # `Self@Bus` vs. `Bus` here is a known pyright edge case when the base
        # class is only resolvable across modules via a TYPE_CHECKING import
        # (see context/thread_context_bus.py) — correct at runtime and covered
        # by tests/test_thread_context_bus.py.
        return ThreadContextBus(self, copy_context())  # pyright: ignore[reportArgumentType]


class Feature(ContextConsumer, ABC, Generic[TypeDTO, TypeDTOResponse, ContextT]):
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

    def __init__(self, *args, **kwargs):
        """
        Initialize the Feature. Dependencies are injected automatically by the framework.
        """
        self._context_binder = None
        self._context_fallback: dict = {}

    @abstractmethod
    def execute(self, dto: TypeDTO) -> TypeDTOResponse | None:
        """
        Execute the feature's business logic.

        Args:
            dto: The input Data Transfer Object containing request parameters

        Returns:
            A response DTO containing the operation result, or None
        """


class ApplicationService(ContextConsumer, ABC, Generic[TypeDTO, TypeDTOResponse, ContextT]):
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

    feature_bus: Bus

    def __init__(self, feature_bus: Bus, *args, **kwargs):
        """
        Initialize the ApplicationService with feature_bus for orchestration.
        Additional dependencies are injected automatically by the framework.
        """
        self.feature_bus = feature_bus
        self._context_binder = None
        self._context_fallback: dict = {}

    @abstractmethod
    def execute(self, dto: TypeDTO) -> TypeDTOResponse | None:
        """
        Execute the application service orchestration logic.

        Args:
            dto: The input Data Transfer Object containing orchestration parameters

        Returns:
            A response DTO containing the orchestration result, or None
        """
