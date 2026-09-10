"""Framework introspection: read a built UseFramework's registries, described.

Shared by entrypoints (exposes a wire protocol) and any other consumer that
needs to read the registries. Nobody owns this; everybody reads it.
"""

from sincpro_framework.introspection.inspector import (
    DtoMetadata,
    DtoName,
    FeatureOrAppServiceMetadata,
    app_services,
    built_bus,
    dtos,
    features,
)

__all__ = [
    "DtoMetadata",
    "DtoName",
    "FeatureOrAppServiceMetadata",
    "app_services",
    "built_bus",
    "dtos",
    "features",
]
