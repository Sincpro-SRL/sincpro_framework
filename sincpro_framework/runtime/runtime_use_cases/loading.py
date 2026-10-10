"""Loading a stored use case onto a bus: its source compiled as a module of its own, its one
handler found, and registered for the Command its `execute` declares.

Context: the module is `sincpro_runtime.<context>.<name>`, in `sys.modules` like any other, so
Pydantic resolves its annotations and the bus routes its Commands by that name. Its filename,
`<runtime <context>.<name> v<version> #<checksum>>`, is kept in `linecache`, so a traceback
shows the stored line that failed — one entry per source, so a draft checked under the same
version never shows its lines in a traceback of the one in force. Every generation compiles the source again: its classes are new ones, and a
bus built before keeps the classes it was built with.
"""

import inspect
import linecache
import sys
from types import ModuleType
from typing import get_type_hints

from sincpro_framework.runtime.runtime_use_cases.domain import RuntimeUseCase, UseCaseRefused
from sincpro_framework.sincpro_abstractions import ApplicationService, Feature
from sincpro_framework.use_bus import UseFramework


def module_name(context: str, use_case: RuntimeUseCase) -> str:
    return f"sincpro_runtime.{context}.{use_case.name}"


def _compiled(context: str, use_case: RuntimeUseCase) -> ModuleType:
    name = module_name(context, use_case)
    filename = (
        f"<runtime {context}.{use_case.name} v{use_case.version} #{use_case.checksum[:8]}>"
    )
    module = ModuleType(name)
    module.__file__ = filename
    lines = use_case.source.splitlines(keepends=True)
    linecache.cache[filename] = (len(use_case.source), None, lines, filename)
    sys.modules[name] = module
    exec(compile(use_case.source, filename, "exec"), vars(module))
    return module


def _handler_in(module: ModuleType) -> type:
    handlers = sorted(
        (
            value
            for value in vars(module).values()
            if isinstance(value, type)
            and issubclass(value, (Feature, ApplicationService))
            and value.__module__ == module.__name__
        ),
        key=lambda handler: handler.__name__,
    )
    if not handlers:
        raise UseCaseRefused("it defines no Feature or ApplicationService")
    if len(handlers) > 1:
        names = ", ".join(handler.__name__ for handler in handlers)
        raise UseCaseRefused(
            f"it defines {names} — one Feature or ApplicationService per use case"
        )
    return handlers[0]


def _command_of(handler: type) -> type:
    parameters = list(inspect.signature(handler.execute).parameters)
    command = (
        get_type_hints(handler.execute).get(parameters[1]) if len(parameters) > 1 else None
    )
    if not isinstance(command, type):
        raise UseCaseRefused(
            f"{handler.__name__}.execute(self, dto: ...) must say which Command it answers"
        )
    return command


def _kind(handler: type) -> str:
    return "Feature" if issubclass(handler, Feature) else "ApplicationService"


def _replaced(
    bus: UseFramework, use_case: RuntimeUseCase, handler: type, command: type
) -> type | None:
    answering = bus.handler_of(command)
    if use_case.replaces is None:
        if answering is not None:
            qualified = f"{answering.__module__}.{answering.__qualname__}"
            raise UseCaseRefused(
                f"{answering.__name__} answers {command.__name__} in code — say "
                f'replaces="{qualified}" to answer it instead'
            )
        return None
    if answering is None:
        raise UseCaseRefused(
            f"it replaces {use_case.replaces}, but nothing answers {command.__name__}"
        )
    if f"{answering.__module__}.{answering.__qualname__}" != use_case.replaces:
        raise UseCaseRefused(
            f"it replaces {use_case.replaces}, but {answering.__name__} answers "
            f"{command.__name__}"
        )
    if _kind(answering) != _kind(handler):
        raise UseCaseRefused(
            f"{answering.__name__} is an {_kind(answering)}; {handler.__name__}, a "
            f"{_kind(handler)}, cannot replace it"
        )
    return answering


def load(bus: UseFramework, context: str, use_case: RuntimeUseCase) -> None:
    """Compile `use_case` and register its handler on `bus`, not built yet.

    Context: the source is code nobody reviewed with the service, so whatever goes wrong in it —
    its syntax, its imports, its module body, its registration — is refused in its name.
    """
    try:
        module = _compiled(context, use_case)
        handler = _handler_in(module)
        command = _command_of(handler)
        register = bus.feature if issubclass(handler, Feature) else bus.app_service
        register(command, replaces=_replaced(bus, use_case, handler, command))(handler)
    except Exception as error:
        raise UseCaseRefused(f"{use_case.name} v{use_case.version}: {error}") from error
