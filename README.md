## Sincpro Framework (Application layer framework)

The main goal of this framework is create a `proxy` and `facade` component/entity, using a pattern `bus` components that wraps all the application layer, allowing to the developer import all the business logic ready to be executed in any context

### Main features:
- Allow to inject `dependencies` provided by the user
- Allow to inject `error handler` at the top level
- Execute the business logic in a `bus` component only providing the DTO(`Data transfer object`)
- Allow to execute decoupled (`use case`, `feature` , `business logic`)
  - using the component `Feature` 
- Allow to execute several `use cases` based on abstraction called `ApplicationService`
  - This one contains the `feature bus` and the developer will be able to execute the `use case` in any context 

#### Example usage

#### Init the framework

This framework provide a command bus that will provide to the developer register use cases

- `Feature` -> This component will be used to execute decoupled business logic
- `ApplicationService` -> This component has injected the `feature bus` and the developer will be able 
to execute the registered `Feature`

```python
# Import the framework
from sincpro_framework import (
  UseFramework, 
  ApplicationService, 
  DataTransferObject,
  Feature as _Feature
)

# Define exceptions this is optional but is a good practice
class GlobalError(Exception): pass
class AppServiceError(Exception): pass
class FeatureError(Exception): pass


# Some dependencies as function
def call_print(algo):
    print(f"Calling from closure {algo}")


def global_error_handler(error):
    """
    Global layer to handle an error
    This function will catch all the exceptions that were
    raised 
    """
    if isinstance(error, GlobalError):
        print("ERROR HANDLEADO")
    else:
        raise Exception("ERROR NO HANDLEADO")


    
def app_error_handler(error):
    """
    We add a handler for `application service` errors
    """
    if isinstance(error, AppServiceError):
        print("APPLICATION HANDLER ERROR")
        raise GlobalError("ERROR DESEDE APPLICATION")
    else:
        raise Exception("ERROR NO HANDLEADO")


def feat_error_handler(error):
    """
    
    """
    if isinstance(error, FeatureError):
        print("FEATURE HANDLER ERROR")
        raise AppServiceError("ERROR DESEDE FEATURE")
    else:
        raise Exception("ERROR NO HANDLEADO")


framework_sp = UseFramework()
# You can add as many dependencies as you want
# Also need to provide a name to the dependency, so in the context of the framework 
# you will be able to call the injedted dependency
framework_sp.add_dependency("andres", call_print)
framework_sp.add_global_error_handler(global_error_handler)
framework_sp.add_app_service_error_handler(app_error_handler)
framework_sp.add_feature_error_handler(feat_error_handler)

# -------------------------------------------- #
# Buena practica en caso de querer tipar las injecciones de dependiencias
# se sugiere crear una clase `Feature` o `ApplicationService` que hereden de las abtracciones
# por ejemplo estamos importando `Feature` original pero como `_Feature` para no confundir
from typing import TypeAlias
class Feature(_Feature, ABC):
    andres: TypeAlias = call_print
    
# de esta forma el IDE o editor nos ayudara a ver los tipos de las injecciones de dependencias
# Recordar utilizar la clase Feature de este modulo en lugar de `sincpro_framework.Feature` ya que este contiene 
# los tipos de las injecciones de dependencias y normalmente este contiene toda la configuracion 
    
```

once you initialize the bus, you will be able to import the framework in your application layer

this framework should be used in the `application layer`

**pattern to import the framework** follow the next snippet 
```python
# Path file: src.apps.siat_api
# Application layer main entry: __init__.py
# import the initialized framework
from src.apps.some_application.infrastructure.sp_framework import (
    ApplicationService,
    DataTransferObject,
    Feature,
    framework_sp,
)

# the `use_case` represent the application layer inside of it
# you should find all the `Feature` and `ApplicationService`
from .use_case import *

# Require to export to be consumed for the infrastructure layer or the more external layer
__all__ = [
    "use_case",
    "framework_sp",
    "Feature",
    "DataTransferObject",
    "ApplicationService",
]



# then import the framework_sp from any another consumer
```

Once you have the framework imported you will be able to register the `use cases`(Features or ServiceApplication)

```python

from src.apps.some_application import framework_sp

# -------------------------------------------- #
#  Feature
# -------------------------------------------- #

@dataclass(kw_only=True)
class CommandRegisterServiceConfiguration(DataTransferObject):
    comm: str


@dataclass(kw_only=True)
class ResponseRegisterServiceConfiguration(DataTransferObject):
    comm: str


@framework_sp.feature(CommandRegisterServiceConfiguration)
class RegisterServiceConfiguration(Feature):
    def execute(
        self, dto: CommandRegisterServiceConfiguration
    ) -> ResponseRegisterServiceConfiguration:
        raise FeatureError("EError de feature")
        return ResponseRegisterServiceConfiguration(comm=dto.comm)

# -------------------------------------------- #
#  Application service
# -------------------------------------------- #
@dataclass(kw_only=True)
class CommandRegisterServiceConfiguration2(DataTransferObject):
    comm: str


@dataclass(kw_only=True)
class ResponseRegisterServiceConfiguration(DataTransferObject):
    comm: str


@framework_sp.app_service(CommandRegisterServiceConfiguration2)
class RegisterServiceConfiguration(ApplicationService):
    def execute(
        self, dto: CommandRegisterServiceConfiguration2
    ) -> ResponseRegisterServiceConfiguration:
        self.andres("Hello") # -> Dependency injected by the user
        self.feature_bus.execute(CommandRegisterServiceConfiguration(comm="Hello")) #-> Decoupled feature
        raise AppServiceError("Testing Global error")
        return ResponseRegisterServiceConfiguration(comm=dto.comm)



```