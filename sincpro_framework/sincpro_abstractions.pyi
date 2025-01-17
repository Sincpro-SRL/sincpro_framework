import abc
from abc import ABC, abstractmethod
from typing import Type, TypeVar, overload

from pydantic import BaseModel

class DataTransferObject(BaseModel): ...

TypeDTO = TypeVar("TypeDTO", bound="DataTransferObject")
TypeDTOResponse = TypeVar("TypeDTOResponse", bound="DataTransferObject")

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

class Feature(ABC, metaclass=abc.ABCMeta):
    def __init__(self, *args, **kwargs) -> None: ...
    @abstractmethod
    def execute(self, dto: TypeDTO) -> TypeDTOResponse | None: ...

class ApplicationService(ABC, metaclass=abc.ABCMeta):
    feature_bus: Bus
    def __init__(self, feature_bus: Bus, *args, **kwargs) -> None: ...
    @abstractmethod
    def execute(self, dto: TypeDTO) -> TypeDTOResponse | None: ...
