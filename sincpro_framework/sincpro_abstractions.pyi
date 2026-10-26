import abc
from abc import ABC, abstractmethod
from typing import Any, Generic, Type, overload

from _typeshed import DataclassInstance
from pydantic import BaseModel
from typing_extensions import TypeVar

from .aio import AsyncBus
from .bus import FeatureBus

class DataTransferObject(BaseModel): ...

TypeDTO = TypeVar("TypeDTO", bound="DataTransferObject | DataclassInstance")
TypeDTOResponse = TypeVar(
    "TypeDTOResponse", bound="DataTransferObject | DataclassInstance | list | dict"
)
ContextT = TypeVar("ContextT")

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
    def thread_context(self) -> Bus:
        """
        This bus, bound to the context of the calling thread — for a pool the project owns.

        A `ThreadPoolExecutor` hands a worker none of the caller's context: a plain
        `executor.submit(bus.execute, dto)` runs with an empty `self.context`. The bus answered
        runs every `execute` in a copy of the context taken here (`in_context`), whichever
        thread calls it — hand its `.execute` to the executor.

        Call it once per task you submit, not once for a whole batch: a copied context can be
        entered by one thread at a time. `ContextExecutor` takes the copy per task on its own.
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

    Every bounded context wraps it once, in its framework.py, and its Features inherit that base:

        from sincpro_framework import Feature as _Feature

        class Feature(_Feature, DependencyContextType):
            ...                                  # the base of this bounded context

    `self.context` is typed with a third parameter, `Feature[Command, Response, Context]` — the
    parameters are given all three or none.
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

    Every bounded context wraps it once, in its framework.py, as it wraps Feature:

        from sincpro_framework import ApplicationService as _ApplicationService

        class ApplicationService(_ApplicationService, DependencyContextType):
            ...                                  # the base of this bounded context
    """

    context: ContextT
    feature_bus: FeatureBus
    def __init__(self, feature_bus: FeatureBus, *args, **kwargs) -> None: ...
    def bind_to_framework(self, binder: Any) -> None: ...
    @abstractmethod
    def execute(self, dto: TypeDTO) -> TypeDTOResponse | None: ...

    # Note: Injected dependencies become available as attributes at runtime
    # The actual dependency attributes are added dynamically by the framework
    # Custom ApplicationService classes should define typed attributes for IDE support
    def __getattr__(self, name: str) -> Any: ...
