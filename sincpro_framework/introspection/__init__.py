"""Framework introspection: read a built UseFramework's registries, described.

Shared by entrypoints (exposes a wire protocol) and any other consumer that
needs to read the registries. Nobody owns this; everybody reads it.

    features(billing)["CommandIssue"].description        ->  "Issue an invoice …"
    describe(billing, CommandIssue).interceptors          ->  ("billing.audit.logged",)
    dtos(billing)["CommandIssue"].type                    ->  CommandIssue
    operations_of(billing)["CommandIssue"].required_input ->  {"invoice_id"}
"""

from sincpro_framework.introspection.inspector import (
    DtoMetadata,
    DtoName,
    FeatureOrAppServiceMetadata,
    app_services,
    built_bus,
    describe,
    dtos,
    features,
)
from sincpro_framework.introspection.operations import Operation, operations_of

__all__ = [
    "DtoMetadata",
    "DtoName",
    "FeatureOrAppServiceMetadata",
    "Operation",
    "app_services",
    "built_bus",
    "describe",
    "dtos",
    "features",
    "operations_of",
]
