"""Read a built UseFramework instance's registries and describe them.

Single place that knows the bus's internal shape (feature_bus.feature_registry,
app_service_bus.app_service_registry, dto_registry) and how to describe a
registered Feature/ApplicationService/DTO. generate_documentation and
entrypoints both build on this same metadata; neither reaches into
FrameworkBus or resolves docstrings on its own.
"""

import inspect
from collections.abc import Mapping

from sincpro_framework.bus import FrameworkBus
from sincpro_framework.sincpro_abstractions import (
    ApplicationService,
    DataTransferObject,
    Feature,
)
from sincpro_framework.use_bus import UseFramework

NOT_BUILT = "Framework must be built before introspection"

type DtoName = str


class FeatureOrAppServiceMetadata(DataTransferObject):
    """One Feature or ApplicationService registered on the bus."""

    name: DtoName
    type: type
    instance: Feature | ApplicationService
    dto: type[DataTransferObject]
    description: str


class DtoMetadata(DataTransferObject):
    """One DTO registered on the bus, as a Feature or ApplicationService input."""

    name: DtoName
    type: type[DataTransferObject]
    description: str | None


def _own_docstring(cls: type) -> str | None:
    """Docstring declared on this class, not inherited from a base class."""
    raw = cls.__dict__.get("__doc__")
    if raw and str(raw).strip():
        return inspect.cleandoc(raw)
    return None


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


def built_bus(framework_instance: UseFramework) -> FrameworkBus:
    if not framework_instance.was_initialized or framework_instance.bus is None:
        raise ValueError(NOT_BUILT)
    return framework_instance.bus


def _describe_all(
    registry: Mapping[DtoName, Feature | ApplicationService],
    dto_registry: Mapping[DtoName, type[DataTransferObject]],
) -> dict[DtoName, FeatureOrAppServiceMetadata]:
    metadata: dict[DtoName, FeatureOrAppServiceMetadata] = {}
    for name, instance in registry.items():
        dto_type = dto_registry.get(name)
        if dto_type is None:
            continue
        feature_or_app_type = instance.__class__
        metadata[name] = FeatureOrAppServiceMetadata(
            name=name,
            type=feature_or_app_type,
            instance=instance,
            dto=dto_type,
            description=_resolve_description(feature_or_app_type, dto_type, name),
        )
    return metadata


def features(framework_instance: UseFramework) -> dict[DtoName, FeatureOrAppServiceMetadata]:
    """Feature registry keyed by DTO name, described."""
    bus = built_bus(framework_instance)
    return _describe_all(bus.feature_bus.feature_registry, bus.dto_registry)


def app_services(
    framework_instance: UseFramework,
) -> dict[DtoName, FeatureOrAppServiceMetadata]:
    """ApplicationService registry keyed by DTO name, described."""
    bus = built_bus(framework_instance)
    return _describe_all(bus.app_service_bus.app_service_registry, bus.dto_registry)


def dtos(framework_instance: UseFramework) -> dict[DtoName, DtoMetadata]:
    """DTO classes keyed by name, feature and application service DTOs both included."""
    bus = built_bus(framework_instance)
    return {
        name: DtoMetadata(name=name, type=dto_type, description=_own_docstring(dto_type))
        for name, dto_type in bus.dto_registry.items()
    }
