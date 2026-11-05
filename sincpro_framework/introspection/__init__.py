"""Framework introspection: read a built UseFramework's registries, described.

Shared by entrypoints (exposes a wire protocol) and any other consumer that
needs to read the registries. Nobody owns this; everybody reads it.

    features(billing)["CommandIssue"].description        ->  "Issue an invoice …"
    describe(billing, CommandIssue).interceptors          ->  ("billing.audit.logged",)
    dtos(billing)["CommandIssue"].type                    ->  CommandIssue
    operations_of(billing)["CommandIssue"].required_input ->  {"invoice_id"}
    operations_of(forge)["CommandMoveIssue"].entities     ->  (Issue, Project)
    role_of(QueryGetIssue)                                ->  Role.GET: what it is for its entity
    metamodel_of(billing)["models"]["Invoice"]            ->  its contract: JSON Schema + x-sincpro
    export_metamodel(billing, "web/metamodel.json")       ->  its version, the file written
"""

from sincpro_framework.introspection.entity_tags import Role, entity_tags, role_of
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
from sincpro_framework.introspection.metamodel import export_metamodel, metamodel_of
from sincpro_framework.introspection.operations import OperationDescription, operations_of

__all__ = [
    "DtoMetadata",
    "DtoName",
    "FeatureOrAppServiceMetadata",
    "OperationDescription",
    "Role",
    "app_services",
    "built_bus",
    "describe",
    "dtos",
    "entity_tags",
    "export_metamodel",
    "features",
    "metamodel_of",
    "operations_of",
    "role_of",
]
