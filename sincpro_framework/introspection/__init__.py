"""Framework introspection: read a built UseFramework's registries, described.

Shared by generate_documentation (renders docs) and entrypoints (exposes a
wire protocol). Neither owns this; both read it.
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
