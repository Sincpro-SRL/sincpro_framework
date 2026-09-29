"""Driving adapters: the buses' use cases on a wire. Shared: `catalog` (one bus as a filtered list
of use cases), `gateway` (N buses, automatic or declared — what every wire extends), `exposure`
(the declared exposure of PRD_14: bindings, their decorators, `internal`, the `Wire` port).
Wires: `rpc` (JSON-RPC 2.0), `grpc`, `mcp`, `rest` (OpenAPI 3.1).

The exposure decorators (`rest`, `rpc`, `grpc`, `mcp`, `queue`) are imported from
`sincpro_framework.entrypoints.exposure`, never from here: their names are this package's wires.
"""

from sincpro_framework.entrypoints.catalog import Catalog, PackedFeatureOrAppService
from sincpro_framework.entrypoints.exposure import (
    Binding,
    Deprecation,
    Exposure,
    ExposureRefused,
    Group,
    GrpcBinding,
    ManifestEntry,
    McpBinding,
    Operation,
    QueueBinding,
    Resolved,
    RestBinding,
    RpcBinding,
    Wire,
    declare,
    internal,
    is_internal,
)
from sincpro_framework.entrypoints.gateway import DEFAULT_LAYERS, Buses, Gateway

__all__ = [
    "Binding",
    "Buses",
    "Catalog",
    "DEFAULT_LAYERS",
    "Deprecation",
    "Exposure",
    "ExposureRefused",
    "Gateway",
    "Group",
    "GrpcBinding",
    "ManifestEntry",
    "McpBinding",
    "Operation",
    "PackedFeatureOrAppService",
    "QueueBinding",
    "Resolved",
    "RestBinding",
    "RpcBinding",
    "Wire",
    "declare",
    "internal",
    "is_internal",
]
