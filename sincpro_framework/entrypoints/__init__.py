"""Entrypoints: everything that starts the application layer from outside — a wire (REST,
JSON-RPC, gRPC, MCP, FastAPI, a FastStream broker), the clock (`cron`) or workers that keep loops
running — and the exposure a project declares on its use cases.

Importing this package loads no extra: each wire lives in `adapters/` and is imported by whoever
serves it.
"""

from sincpro_framework.entrypoints.domain.bindings import (
    Binding,
    Deprecation,
    GrpcBinding,
    McpBinding,
    QueueBinding,
    RestBinding,
    RpcBinding,
)
from sincpro_framework.entrypoints.domain.surface import (
    Exposure,
    Group,
    ManifestEntry,
    Operation,
    Resolved,
    Wire,
)
from sincpro_framework.entrypoints.entrypoint.catalog import (
    Catalog,
    PackedFeatureOrAppService,
)
from sincpro_framework.entrypoints.entrypoint.decorators import (
    declare,
    grpc,
    mcp,
    queue,
    rest,
    rpc,
)
from sincpro_framework.entrypoints.entrypoint.gateway import Buses, Gateway
from sincpro_framework.entrypoints.entrypoint.internal import internal, is_internal
from sincpro_framework.entrypoints.entrypoint.workers import Loop, Poll, Process

__all__ = [
    "Binding",
    "Buses",
    "Catalog",
    "Deprecation",
    "Exposure",
    "Gateway",
    "Group",
    "GrpcBinding",
    "Loop",
    "ManifestEntry",
    "McpBinding",
    "Operation",
    "PackedFeatureOrAppService",
    "Poll",
    "Process",
    "QueueBinding",
    "Resolved",
    "RestBinding",
    "RpcBinding",
    "Wire",
    "declare",
    "grpc",
    "internal",
    "is_internal",
    "mcp",
    "queue",
    "rest",
    "rpc",
]
