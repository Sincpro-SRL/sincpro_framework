from abc import ABC, abstractmethod
from typing import Type, TypeVar

from pydantic import BaseModel, ConfigDict

TypeDTO = TypeVar("TypeDTO", bound="DataTransferObject")
TypeDTOResponse = TypeVar("TypeDTOResponse", bound="DataTransferObject")


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
    """Feature is the first layer of the framework, it is the main abstraction to execute a business logic"""

    def __init__(self, *args, **kwargs):
        pass

    @abstractmethod
    def execute(self, dto: TypeDTO) -> TypeDTOResponse | None:
        pass


class ApplicationService(ABC):
    """Second layer of the framework, orchestration of features"""

    feature_bus: Bus

    def __init__(self, feature_bus: Bus, *args, **kwargs):
        self.feature_bus = feature_bus

    @abstractmethod
    def execute(self, dto: TypeDTO) -> TypeDTOResponse | None:
        pass
