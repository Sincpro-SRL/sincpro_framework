from typing import Any, Callable, overload

from _typeshed import Incomplete

from . import ioc as ioc
from .bus import FrameworkBus as FrameworkBus
from .exceptions import DependencyAlreadyRegistered as DependencyAlreadyRegistered
from .exceptions import SincproFrameworkNotBuilt as SincproFrameworkNotBuilt
from .sincpro_abstractions import TypeDTO as TypeDTO
from .sincpro_abstractions import TypeDTOResponse as TypeDTOResponse
from .sincpro_logger import create_logger as create_logger

class UseFramework:
    feature: Incomplete
    app_service: Incomplete
    dynamic_dep_registry: Incomplete
    global_error_handler: Incomplete
    feature_error_handler: Incomplete
    app_service_error_handler: Incomplete
    was_initialized: bool
    bus: Incomplete
    def __init__(self, bundled_context_name: str = "sincpro_framework") -> None: ...
    def __call__(self, dto: TypeDTO) -> None: ...
    @overload
    def __call__(
        self, dto: TypeDTO, return_type: type[TypeDTOResponse]
    ) -> TypeDTOResponse: ...
    def build_root_bus(self) -> None: ...
    def add_dependency(self, name, dep: Any): ...
    def add_global_error_handler(self, handler: Callable): ...
    def add_feature_error_handler(self, handler: Callable): ...
    def add_app_service_error_handler(self, handler: Callable): ...
    @property
    def logger(self) -> Any: ...
