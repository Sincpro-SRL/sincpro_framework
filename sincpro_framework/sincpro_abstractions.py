from abc import ABC, abstractmethod
from typing import Any, TypeVar

from pydantic import BaseModel


class DataTransferObject(BaseModel):
    """
    Abstraction that represent a object that will travel through to any layer
    """

    model_config = {"arbitrary_types_allowed": True}


TypeDTO = TypeVar("TypeDTO", bound="DataTransferObject")
TypeDTOResponse = TypeVar("TypeDTOResponse", bound="DataTransferObject")


class Bus(ABC):
    @abstractmethod
    def execute(self, dto: TypeDTO) -> TypeDTO | TypeDTOResponse | Any | None:
        """Main method to execute a dto"""


class Feature(Bus):
    """Feature is the first layer of the framework, it is the main abstraction to execute a business logic"""

    def __init__(self, *args, **kwargs):
        pass


class ApplicationService(Bus):
    """Second layer of the framework, orchestration of features"""

    feature_bus: Bus

    def __init__(self, feature_bus: Bus, *args, **kwargs):
        self.feature_bus = feature_bus

    @abstractmethod
    def execute(self, dto: TypeDTO) -> TypeDTO | None:
        """Main difference between Feature and ApplicationService can execute features internally"""
