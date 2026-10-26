"""Loading a stored use case onto a bus: its source compiled as a module of its own, its one
handler found, and registered for the Command its `execute` declares.

Context: the module is `sincpro_runtime.<context>.<name>`, in `sys.modules` like any other, so
Pydantic resolves its annotations and the bus routes its Commands by that name. Its filename,
`<runtime <context>.<name> v<version> #<checksum>>`, is stored in `linecache`, so a traceback
shows the stored line that failed — one entry per source, so a draft checked under the same
version never shows its lines in a traceback of the one in force. Every generation compiles
the source again: its classes are new ones, and a bus built before retains the classes it was
built with.
"""

import inspect
import linecache
import sys
from types import ModuleType
from typing import get_type_hints

from sincpro_framework.runtime.runtime_use_cases import exceptions
from sincpro_framework.runtime.runtime_use_cases.domain import RuntimeUseCase
from sincpro_framework.sincpro_abstractions import ApplicationService, Feature
from sincpro_framework.use_bus import UseFramework


def module_name(context: str, use_case: RuntimeUseCase) -> str:
    """The name the stored use case's module goes by in `sys.modules`.

    module_name("billing", RuntimeUseCase(name="quote", ...))
        ->  "sincpro_runtime.billing.quote"
    """
    return f"sincpro_runtime.{context}.{use_case.name}"


def _compiled(context: str, use_case: RuntimeUseCase) -> ModuleType:
    """Run `use_case.source` as a new module and answer it.

    Context: the module is put in `sys.modules` before its body runs, so its own classes
    resolve their annotations by its name; its lines go to `linecache` under a filename unique
    to this source, so a traceback quotes the stored line.

    1. The filename carries the context, the name, the version and 8 digits of the checksum.
    2. The source's lines are stored in `linecache` under that filename.
    3. A new module is registered in `sys.modules` under `module_name`.
    4. The source is compiled with that filename and run in the module's namespace.
    Final: the module — one whose body raised stays in `sys.modules`; the registry settles it.
    """
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
    """The one Feature or ApplicationService the module defines — one it imports is not its
    own.

    Context: a stored use case is one handler; none, or more than one, is refused with their
    names, so the author knows which to remove.
    """
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
        raise exceptions.UseCaseRefused("it defines no Feature or ApplicationService")
    if len(handlers) > 1:
        names = ", ".join(handler.__name__ for handler in handlers)
        raise exceptions.UseCaseRefused(
            f"it defines {names} — one Feature or ApplicationService per use case"
        )
    return handlers[0]


def _command_of(handler: type) -> type:
    """The Command `handler` answers — the annotation of the parameter after `self` in its
    `execute`; refused when it has none or it is not a class.

        class Quote(Feature):
            def execute(self, dto: CommandQuote) -> ResponseQuote: ...

        _command_of(Quote)   ->  CommandQuote
    """
    parameters = list(inspect.signature(handler.execute).parameters)
    command = (
        get_type_hints(handler.execute).get(parameters[1]) if len(parameters) > 1 else None
    )
    if not isinstance(command, type):
        raise exceptions.UseCaseRefused(
            f"{handler.__name__}.execute(self, dto: ...) must say which Command it answers"
        )
    return command


def _kind(handler: type) -> str:
    """`"Feature"` or `"ApplicationService"` — the two can only replace their own kind."""
    return "Feature" if issubclass(handler, Feature) else "ApplicationService"


def _replaced(
    bus: UseFramework, use_case: RuntimeUseCase, handler: type, command: type
) -> type | None:
    """The handler in code `handler` answers `command` instead of — `None` when it answers a
    Command nobody else does.

    Context: replacing a use case is said out loud, never by accident.

    1. With no `replaces`, a Command already answered on `bus` is refused, naming the
       `replaces=` to write; an unanswered one answers `None`.
    2. With `replaces`, the Command must be answered, by exactly the `module.Class` named.
    3. The handler answering and the stored one must be of the same kind.
    Final: the handler answering, for `bus.feature(..., replaces=)` to retire.
    """
    answering = bus.handler_of(command)
    if use_case.replaces is None:
        if answering is not None:
            qualified = f"{answering.__module__}.{answering.__qualname__}"
            raise exceptions.UseCaseRefused(
                f"{answering.__name__} answers {command.__name__} in code — say "
                f'replaces="{qualified}" to answer it instead'
            )
        return None
    if answering is None:
        raise exceptions.UseCaseRefused(
            f"it replaces {use_case.replaces}, but nothing answers {command.__name__}"
        )
    if f"{answering.__module__}.{answering.__qualname__}" != use_case.replaces:
        raise exceptions.UseCaseRefused(
            f"it replaces {use_case.replaces}, but {answering.__name__} answers "
            f"{command.__name__}"
        )
    if _kind(answering) != _kind(handler):
        raise exceptions.UseCaseRefused(
            f"{answering.__name__} is an {_kind(answering)}; {handler.__name__}, a "
            f"{_kind(handler)}, cannot replace it"
        )
    return answering


def load(bus: UseFramework, context: str, use_case: RuntimeUseCase) -> None:
    """Compile `use_case` and register its handler on `bus`, not built yet.

    Context: the source is code nobody reviewed with the service, so whatever goes wrong in it —
    its syntax, its imports, its module body, its registration — is refused in its name.

        load(billing, "billing", RuntimeUseCase(name="quote", source=QUOTE_SOURCE))
            ->  None; sincpro_runtime.billing.quote compiled, its Feature on CommandQuote
        load(billing, "billing", RuntimeUseCase(name="quote", source="def broken(:"))
            ->  UseCaseRefused("quote v1: invalid syntax …")

    A Feature is registered as a feature, anything else as an application service; one that
    answers a Command code already answers needs `replaces=` naming it.
    """
    try:
        module = _compiled(context, use_case)
        handler = _handler_in(module)
        command = _command_of(handler)
        register = bus.feature if issubclass(handler, Feature) else bus.app_service
        register(command, replaces=_replaced(bus, use_case, handler, command))(handler)
    except Exception as error:
        raise exceptions.UseCaseRefused(
            f"{use_case.name} v{use_case.version}: {error}"
        ) from error
