"""Read a built UseFramework instance's registries and describe them.

Single place that knows the bus's internal shape (feature_bus.feature_registry,
app_service_bus.app_service_registry, dto_registry) and how to describe a
registered Feature/ApplicationService/DTO. entrypoints builds on this same
metadata; it never reaches into FrameworkBus or resolves docstrings on its
own.
"""

import inspect
from collections.abc import Mapping
from typing import Any, get_args, get_origin, get_type_hints

from sincpro_framework.bus import FrameworkBus
from sincpro_framework.sincpro_abstractions import (
    ApplicationService,
    DataTransferObject,
    Feature,
)
from sincpro_framework.use_bus import UseFramework

NOT_BUILT = "Framework must be built before introspection"

type DtoName = str


def _own_docstring(cls: type) -> str | None:
    """Docstring declared on this class, not inherited from a base class."""
    raw = cls.__dict__.get("__doc__")
    if raw and str(raw).strip():
        return inspect.cleandoc(raw)
    return None


class FeatureOrAppServiceMetadata(DataTransferObject):
    """One Feature or ApplicationService registered on the bus.

    `dto` is a plain `type`, not `type[DataTransferObject]`: the bus's own
    `TypeDTO` admits a dataclass, and introspection describes what is registered
    rather than refusing it.

    `response` is what `execute` declares it answers — a DTO, a dataclass, an
    `Entity`, `list[...]`, or `None` when nothing is declared. It is an
    annotation, not necessarily a class, so consumers schema it through
    Pydantic rather than reading `__name__` off it.
    """

    name: DtoName
    type: type
    instance: Feature | ApplicationService
    dto: type
    description: str
    response: Any | None = None


class DtoMetadata(DataTransferObject):
    """One DTO registered on the bus, as a Feature or ApplicationService input.

    `type` is a plain `type` for the same reason `FeatureOrAppServiceMetadata.dto` is.
    """

    name: DtoName
    type: type
    description: str | None


def built_bus(framework_instance: UseFramework) -> FrameworkBus:
    if not framework_instance.was_initialized or framework_instance.bus is None:
        raise ValueError(NOT_BUILT)
    return framework_instance.bus


def _resolve_description(feature_or_app_type: type, dto_type: type, dto_name: DtoName) -> str:
    """Pick a description without inheriting the Feature/ApplicationService base essay.

    1. Prefer the Feature/ApplicationService class's own docstring.
    2. Else the execute method's own docstring.
    3. Else the input DTO's own docstring.
    4. Final: the DTO class name.
    """
    execute = feature_or_app_type.__dict__.get("execute")
    execute_doc = (
        inspect.cleandoc(execute.__doc__) if execute is not None and execute.__doc__ else None
    )
    return (
        _own_docstring(feature_or_app_type)
        or execute_doc
        or _own_docstring(dto_type)
        or dto_name
    )


def _resolve_response(feature_or_app_type: type) -> Any | None:
    """What this Feature/ApplicationService says it answers.

    The bus never stores it — `execute(dto, return_type)`'s `return_type` is a
    typing hint for the caller, discarded at runtime — so it is read off the
    declaration.

    1. Prefer the execute method's own return annotation.
    2. Else the second parameter of `Feature[Dto, Response, Ctx]`, for a class
       that types the generic but not the method.
    3. Final: None. An undeclared response publishes no result schema, instead of
       a wrong one.
    """
    execute = feature_or_app_type.__dict__.get("execute")
    if execute is not None:
        try:
            hints = get_type_hints(execute)
        except Exception:
            hints = {}
        if "return" in hints:
            return hints["return"]
    for base in getattr(feature_or_app_type, "__orig_bases__", ()):
        if get_origin(base) not in (Feature, ApplicationService):
            continue
        args = get_args(base)
        if len(args) >= 2:
            return args[1]
    return None


def _describe_all(
    registry: Mapping[type, Feature | ApplicationService],
) -> dict[DtoName, FeatureOrAppServiceMetadata]:
    metadata: dict[DtoName, FeatureOrAppServiceMetadata] = {}
    for dto_type, instance in registry.items():
        name = dto_type.__name__
        feature_or_app_type = instance.__class__
        metadata[name] = FeatureOrAppServiceMetadata(
            name=name,
            type=feature_or_app_type,
            instance=instance,
            dto=dto_type,
            description=_resolve_description(feature_or_app_type, dto_type, name),
            response=_resolve_response(feature_or_app_type),
        )
    return metadata


def features(framework_instance: UseFramework) -> dict[DtoName, FeatureOrAppServiceMetadata]:
    """Feature registry keyed by DTO name, described."""
    bus = built_bus(framework_instance)
    return _describe_all(bus.feature_bus.feature_registry)


def app_services(
    framework_instance: UseFramework,
) -> dict[DtoName, FeatureOrAppServiceMetadata]:
    """ApplicationService registry keyed by DTO name, described."""
    bus = built_bus(framework_instance)
    return _describe_all(bus.app_service_bus.app_service_registry)


def dtos(framework_instance: UseFramework) -> dict[DtoName, DtoMetadata]:
    """DTO classes keyed by name, feature and application service DTOs both included."""
    bus = built_bus(framework_instance)
    return {
        name: DtoMetadata(name=name, type=dto_type, description=_own_docstring(dto_type))
        for name, dto_type in bus.dto_registry.items()
    }
