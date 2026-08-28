import abc
from abc import ABC, abstractmethod
from typing import Any, Generic, Type, overload

from pydantic import BaseModel
from typing_extensions import TypeVar

from .aio import AsyncBus as AsyncBus
from .context.thread_context_bus import ThreadContextBus as ThreadContextBus

class DataTransferObject(BaseModel): ...

TypeDTO = TypeVar("TypeDTO", bound="DataTransferObject")
TypeDTOResponse = TypeVar("TypeDTOResponse", bound="DataTransferObject")
ContextT = TypeVar("ContextT")

# Additional TypeVars for better dependency injection typing
TFeature = TypeVar("TFeature", bound="Feature")
TApplicationService = TypeVar("TApplicationService", bound="ApplicationService")

class Bus(ABC, metaclass=abc.ABCMeta):
    log_after_execution: bool
    service_name: str

    @abstractmethod
    @overload
    def execute(
        self, dto: TypeDTO, return_type: Type[TypeDTOResponse]
    ) -> TypeDTOResponse: ...
    @abstractmethod
    @overload
    def execute(self, dto: TypeDTO) -> TypeDTOResponse | None: ...
    def thread_context(self) -> ThreadContextBus:
        """
        Return a handle to this bus bound to the calling thread's current context.

        `context()` overlays live in a ContextVar, which is isolated per OS
        thread. A plain `executor.submit(bus.execute, dto)` runs `execute` in a
        new thread that never saw the overlay's `set()`, so every Feature's
        `self.context` there silently falls back to the (usually empty) shared
        context. Call `thread_context()` here, in the thread that still has the
        overlay active, then hand `.execute` (not the raw bus) to the executor.

        Call it once per task you submit, not once for a whole batch — see
        `ThreadContextBus`.
        """
        ...

    def get_async_bus(self) -> AsyncBus:
        """
        Return a stateless async facade bound to this bus.

        Unlike `thread_context()`, this handle is reusable across concurrent
        calls: call `get_async_bus()` once, then `await`/`asyncio.gather` many
        `.execute()`/`__call__` calls on it — each call captures its own
        context snapshot internally. Use this from a caller that is itself
        `async def` and wants to fan out several DTOs concurrently without
        blocking its event loop.
        """
        ...

class Feature(ABC, Generic[TypeDTO, TypeDTOResponse, ContextT], metaclass=abc.ABCMeta):
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

    context: ContextT

    def __init__(self, *args, **kwargs) -> None: ...
    def bind_to_framework(self, binder: Any) -> None: ...
    @abstractmethod
    def execute(self, dto: TypeDTO) -> TypeDTOResponse | None: ...

    # Note: Injected dependencies become available as attributes at runtime
    # The actual dependency attributes are added dynamically by the framework
    # Custom Feature classes should define typed attributes for IDE support
    def __getattr__(self, name: str) -> Any: ...

class ApplicationService(
    ABC, Generic[TypeDTO, TypeDTOResponse, ContextT], metaclass=abc.ABCMeta
):
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

    context: ContextT
    feature_bus: Bus
    def __init__(self, feature_bus: Bus, *args, **kwargs) -> None: ...
    def bind_to_framework(self, binder: Any) -> None: ...
    @abstractmethod
    def execute(self, dto: TypeDTO) -> TypeDTOResponse | None: ...

    # Note: Injected dependencies become available as attributes at runtime
    # The actual dependency attributes are added dynamically by the framework
    # Custom ApplicationService classes should define typed attributes for IDE support
    def __getattr__(self, name: str) -> Any: ...
