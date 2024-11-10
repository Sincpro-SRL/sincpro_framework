from abc import ABC, abstractmethod

from pydantic import BaseModel


class DataTransferObject(BaseModel):
    """
    Abstraction that represent a object that will travel through to any layer
    """

    model_config = {"arbitrary_types_allowed": True}


class Bus(ABC):
    @abstractmethod
    def execute(self, dto: DataTransferObject) -> DataTransferObject:
        """
        :param dto:
        :return:
        """


class Feature(ABC):
    def __init__(self, *args, **kwargs):
        pass

    @abstractmethod
    def execute(self, dto: DataTransferObject) -> DataTransferObject:
        """
        :param dto: Any command or event
        :return:
        """


class ApplicationService(ABC):
    feature_bus: Bus

    def __init__(self, feature_bus: Bus, *args, **kwargs):
        self.feature_bus = feature_bus

    @abstractmethod
    def execute(self, dto: DataTransferObject) -> DataTransferObject:
        """
        Main difference between Feature and ApplicationService can execute features internally
        :param dto: Any command or event
        :return: Any response
        """
